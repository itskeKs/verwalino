# Architektur- und Datenmodell-Design: M0 (Fundament & Mandantenfähigkeit)

Stand: 2026-06-01 · Version 0.1 (Design, vor Implementierung)
Bezug: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 · [roadmap.md](roadmap.md)
Autor: `engine-correctness` (Opus 4.8) · Status: **Entwurf zur Bestätigung durch den Menschen — noch kein Code, kein Commit**

> **Historischer Hinweis:** Die folgenden Abschnitte dokumentieren den
> M0-Entwurfsstand von 2026-06-01. Sie sind nicht der Status der heutigen
> Implementierung. Der aktuelle, begrenzte Teilungserklärungs-Workflow ist im
> folgenden Abschnitt ergänzt; sein Betriebsvertrag steht im
> [Runbook](operations/declaration-imports.md).

---

## Aktueller Teilungserklärungs-Workflow (2026-08-29)

Die Anwendung verwendet für Teilungserklärungen einen **festen,
deterministischen Manager**, keinen frei autonomen Agenten. Die
`DeclarationPipeline` besitzt die feste Passreihenfolge, Budgets, Deadlines,
Reservations, Retry-Grenzen, lokale Validierung und Veröffentlichung. Ein
Modell erhält keine Tools, keine Hand-offs und keine Aktionssenke. PDF-Text,
OCR und Bildseiten sind untrusted data: Sie dürfen Fakten vorschlagen, aber
weder den Ablauf noch Berechtigungen oder kanonische Daten bestimmen.

```text
zugelassene PDF -> lokale Vorprüfung/OCR -> Luna-Structured-Outputs
  -> exakte lokale Evidence-Quote-Validierung + Domain-Checks
  -> deterministische RouterDecision
  -> optional: ausgewählte Seite lokal als PNG rendern -> Mini-Recheck
  -> erneut validieren -> NEEDS_REVIEW -> Human Approval
```

Luna läuft im Produkt mit `reasoning.effort="medium"`. Der Recheck ist auf
`gpt-5.4-mini` mit `reasoning.effort="none"` und ausschließlich auf
routerselektierte Seiten begrenzt. Luna `low`, Terra und die Variante mit
Mini-Router sind Eval-Konfigurationen, nicht produktive Autopromotion. Jeder
Pass ist unabhängig: Es gibt kein `previous_response_id` und keinen
übernommenen Reasoning-Zustand. Der Provider nutzt Responses API Structured
Outputs, nicht frei geparsten JSON-Text.

Die Evidence-Grenze ist lokal und seitengebunden: Eine Quote wird nur
akzeptiert, wenn sie nach kanonischer Normalisierung als zusammenhängender
Text auf genau der behaupteten Seite vorkommt. Unbekannte oder nicht
renderbare Seiten bleiben bei Human Review. Der reine `RouterDecision`
entscheidet nur aus lokalen Fehlercodes, Seiteninventar, Renderbarkeit und
lokaler Qualitätsmessung; weder Dokumenttext noch Modell-Confidence fließen
ein. Es gibt keinen learned/online Router, solange kein repräsentatives,
gelabeltes Goldset seine Änderung absichert.

Vision-Eskalation ist eine flüchtige lokale Datenübertragung: Erst nach
`VISION_RECHECK` wird die exakt ausgewählte Seite innerhalb der bestehenden
Pixel-, Byte- und Deadline-Grenzen zu PNG gerendert. Schlägt das Rendern fehl,
wird kein Ersatzbild und keine andere Seite übertragen. Danach gelten erneut
alle Evidence- und Domain-Gates. Ein erfolgreicher Lauf endet ausschließlich
in `NEEDS_REVIEW`; **Human Approval** bleibt der einzige Weg in kanonische
Gebäude- oder Wissensdaten.

Das technische Pass-Ledger ist von den Fachdaten getrennt: eine append-only,
RLS-geschützte Zeile je SDK-Attempt, gebunden an
`declaration_extraction_run_id`, enthält ausschließlich allowlist-validierte
technische Metadaten. Der Cache-Schlüssel für Luna besteht ausschließlich aus
statischer Modell-/Pass-/Prompt-/Schema-/Harness-Identität; der statische
Developer-Block steht vor dynamischen Dokumentdaten. Luna nutzt explizites
Caching mit 30 Minuten, Mini kein explizites Caching. Kosten- und
Usage-Partitionen werden vor Persistenz fail closed validiert.

Diese Grenzen orientieren sich an [Harness Engineering](https://openai.com/index/harness-engineering/)
(Repo-Legibilität, erzwingbare Grenzen und Evals statt Autonomie), an den
[Modellrichtlinien](https://developers.openai.com/api/docs/guides/latest-model),
[Vision-Hinweisen](https://developers.openai.com/api/docs/guides/images-vision),
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
und der [Prompt-Injection-Resistenz](https://openai.com/index/designing-agents-to-resist-prompt-injection/).
Routing-Forschung wie [RouteLLM](https://proceedings.iclr.cc/paper_files/paper/2025/file/5503a7c69d48a2f86fc00b3dc09de686-Paper-Conference.pdf)
und [RouterEval](https://aclanthology.org/2025.findings-emnlp.208.pdf) stützt
die Entscheidung, Varianten zu messen und zu vergleichen; sie ersetzt keine
repräsentativen fachlichen Labels.

> Dieses Dokument deckt M0 (und das vorbereitende Datenmodell für M1) ab. Es trifft die tragenden Architekturentscheidungen, die sich später kaum nachrüsten lassen (Mandantentrennung, GoBD-Unveränderbarkeit). Es enthält **kein** Anwendungscode und **keine** Migrations-Implementierung — nur Design, Begründung und einen abarbeitbaren Scaffolding-Plan für `bulk-impl`.
>
> **Verbindlichkeit:** Die SDD (v0.2) und die Roadmap sind die Quelle der fachlichen Wahrheit und bleiben unangetastet. Wo dieses Dokument über die SDD hinausgeht, ist das als *Annahme* oder *offener Punkt* markiert. Punkte, die der Mensch entscheiden muss, sind mit **[ENTSCHEIDUNG]** gekennzeichnet. Die zugehörigen Architecture Decision Records liegen unter [adr/](adr/).

---

## 0. Leitbild und Geltungsbereich

**Fachlicher Anspruch (SDD §1):** Abrechnungssicherheit — nachrechenbare, prüffeste, unveränderbare Ergebnisse. Das Datenmodell trägt diesen Anspruch ab Tag 1, lange bevor die Abrechnungsengine (M3/M4) existiert.

**M0-Exit-Kriterium (Roadmap):** „Zwei Organizations können angelegt werden und sehen garantiert keine Daten der jeweils anderen; Rollen greifen." Dieses Kriterium ist die Messlatte für jede Entscheidung in diesem Dokument.

**Vier projektweite Invarianten** (SDD §4, §6.6, §14, §16; Roadmap-Querschnitt), die ab M0 im Datenmodell verankert und in M9 final gehärtet werden:

| Invariante | Bedeutung | Wo verankert in M0 |
| --- | --- | --- |
| **I1 Mandantentrennung** | Kein Cross-Org-Datenzugriff, hart erzwungen. | RLS + Tenant-Context (Abschnitt 2) |
| **I2 GoBD-Unveränderbarkeit** | Freigegebene Belege/Abrechnungen werden nie überschrieben; Korrektur = Neuversion; Prüfsumme + Audit-Trail. | Append-only-Tabellen, SHA-256, Versionsketten (Abschnitt 3) |
| **I3 Chronologische Integrität** | Historien (Eigentum, Miete) werden nie überschrieben; anteilige Zeiträume bleiben möglich. | Temporale Tabellen mit `valid_from`/`valid_to` (Abschnitt 5) |
| **I4 Geldexaktheit** | Geldbeträge exakt, nie Gleitkomma. | `NUMERIC(14,2)` / `NUMERIC` für Quoten (Abschnitt 5.1) |

Eine fünfte, fachlich eigenständige Achse ist die **Vermögenstrennung** (SDD §4, §6.3): Gelder/Belege verschiedener Mandate bzw. WEGs *innerhalb* einer Organization werden nie vermischt. Sie ist von der Mandantentrennung zu unterscheiden (Abschnitt 2.6) und wird in M0 als Modell-Vorbereitung, aber noch nicht als zweite RLS-Achse umgesetzt.

---

## 1. Architektur-Überblick

### 1.1 Systemgrenzen (M0)

```
            ┌──────────────────────────────────────────────────────────┐
            │                  Responsive Web-App (React/TS)            │
            │         Desktop-first, mobil bedienbar (Browser)          │
            └───────────────────────────┬──────────────────────────────┘
                                         │  HTTPS / JSON (API-first)
                                         ▼
   ┌───────────────────────────────────────────────────────────────────────┐
   │                          Backend (API-first)                           │
   │                                                                         │
   │  ┌───────────────┐   ┌────────────────────┐   ┌──────────────────────┐ │
   │  │ Auth /        │   │  Tenant-Context-    │   │  Domain-Services     │ │
   │  │ Session-Layer │──▶│  Middleware         │──▶│  (M0: Org, User,     │ │
   │  │ (Login, RBAC) │   │  (setzt org_id im   │   │   Mandate)           │ │
   │  └───────────────┘   │   DB-Session-GUC)   │   └──────────┬───────────┘ │
   │                      └────────────────────┘              │             │
   │                                                          ▼             │
   │  ┌──────────────────────────────────────────────────────────────────┐ │
   │  │  Persistenz-Layer (Repository/ORM)                                │ │
   │  │  - Alle Tenant-Queries laufen durch einen Org-gebundenen          │ │
   │  │    DB-Connection-Kontext                                          │ │
   │  └──────────────────────────────────────────────────────────────────┘ │
   └───────────────────────────────────┬───────────────────────────────────┘
                                        │
                       ┌────────────────┴───────────────┐
                       ▼                                 ▼
        ┌──────────────────────────┐      ┌──────────────────────────────┐
        │ PostgreSQL                │      │ Objektspeicher (Belege)       │
        │ - Shared DB               │      │ - M0: lokales FS mit          │
        │ - Row-Level Security      │      │   Prüfsummen                  │
        │ - org_id auf jeder        │      │ - später: S3 mit Object-Lock  │
        │   Tenant-Tabelle          │      │   (WORM) + Versionierung      │
        └──────────────────────────┘      └──────────────────────────────┘
```

In M0 ist nur der linke Kern relevant (Auth, Tenant-Context, Org/User/Mandate, RLS-Fundament). PDF-Rendering-Pipeline und Objektspeicher werden als **Infrastruktur-Skelett** aufgesetzt (Roadmap M0), aber fachlich erst ab M2/M3 gefüllt. Dieses Dokument beschreibt das Belegspeicher- und Snapshot-Konzept (Abschnitt 3) vollständig vor, weil es eine Invariante ist und das Datenmodell prägt — die eigentliche Belege-Implementierung ist M2 (`engine-correctness` liefert dort das vertiefte GoBD-Belegspeicher-Design, Roadmap-Modellzuordnung).

### 1.2 Architekturprinzipien (aus SDD §16, hier konkretisiert)

- **API-first:** Backend-API ist sauber von der Web-UI getrennt; sie dient Integrationen (späterer Bankimport, DATEV-Export), nicht nativen Clients.
- **Schichten:** Auth → Tenant-Context → Domain-Service → Repository → DB. Die Mandantentrennung wird in **zwei** dieser Schichten erzwungen (Repository *und* DB-RLS), siehe Abschnitt 2.
- **Abrechnungslogik getrennt** von UI und PDF-Layout (greift erst ab M3, aber das Schichtenmodell legt es ab M0 an).
- **Unveränderbare Snapshots** für finalisierte Abrechnungen und freigegebene Belege (Abschnitt 3).
- **Kostenkategorien als versionierte Systemdaten** (relevant ab M2; Modell-Vorbereitung in Abschnitt 5.4).

---

## 2. Mandantentrennung (I1 — die wichtigste M0-Entscheidung)

### 2.1 Optionen

| Option | Beschreibung | Isolationsstärke | Betriebskomplexität | Abrechnungs-/Reporting-Eignung |
| --- | --- | --- | --- | --- |
| **A — Shared DB, App-Layer-Scoping** | Eine DB, `organization_id` auf jeder Tabelle, Filter ausschließlich im Anwendungscode. | Schwach: ein vergessenes `WHERE org_id = …` leckt sofort Fremddaten. | Niedrig | Gut |
| **B — Shared DB + PostgreSQL Row-Level Security (RLS)** | Eine DB, `organization_id` auf jeder Tabelle, **DB-erzwungene** RLS-Policies zusätzlich zum App-Scoping. | Stark: DB weist Cross-Org-Zugriff selbst dann ab, wenn der App-Filter fehlt. Defense-in-Depth. | Mittel | Gut (alle Mandate in einer DB; Cross-Org-Reporting für die Verwaltung selbst möglich) |
| **C — Schema-per-Tenant** | Ein PostgreSQL-Schema je Organization, identische Tabellen je Schema. | Stark (physische Trennung pro Schema). | Hoch: Migrationen über N Schemata, Connection-Routing, Schema-Provisionierung pro Neukunde. | Schwächer für mandantenübergreifende Sichten; gut für „eine WEG = eine harte Box". |
| **D — Datenbank-/Cluster-per-Tenant** | Eigene DB je Organization. | Sehr stark. | Sehr hoch; für ein MVP unverhältnismäßig. | Schlecht für Aggregation; teuer. |

### 2.2 Entscheidung: **Option B — Shared DB + RLS + verpflichtendes `organization_id`-Scoping** *(Empfehlung, [ENTSCHEIDUNG])*

**Begründung im Kontext genau dieser Domäne:**

1. **Defense-in-Depth statt Einzelpunkt-Versagen.** Die Mandantentrennung ist hier nicht nur Datenschutz, sondern berührt die *Vermögenstrennung* (Treuhand-/Fremdgeld) — ein Leck ist ein gravierender, potenziell haftungsrelevanter Fehler. Option A hängt an der Disziplin jeder einzelnen Query; ein einziges vergessenes Filter-Statement (oder ein generischer Report, ein Ad-hoc-Skript, ein künftiger Bulk-Endpunkt) leckt Fremddaten. RLS macht die DB zur **letzten, nicht umgehbaren Verteidigungslinie**: Selbst wenn der App-Layer-Filter fehlt, gibt die DB nur Zeilen der aktiven Organization zurück. Die Invariante wird damit zur *Eigenschaft des Speichers*, nicht zur Eigenschaft des Codes.
2. **Niedrigere Betriebslast als Schema-per-Tenant.** Eine professionelle Verwaltung betreut viele Mandate; das Produkt ist mandantenstark, aber pro Tenant nicht extrem datenintensiv. Schema-per-Tenant (C) multipliziert die Migrationskomplexität mit der Tenant-Zahl und verlangt Connection-Routing/Provisionierung — Aufwand, der ohne den proportionalen Isolationsgewinn von RLS kommt.
3. **Reporting/Betrieb der Verwaltung selbst.** Die Verwaltung als Organization will perspektivisch eigene mandatsübergreifende Auswertungen (Fristenmonitor über alle WEGs, offene Posten gesamt). Mit Option B ist das eine einfache, RLS-kontrollierte Query im Org-Kontext; bei C wären es N Schema-Joins.
4. **Migrations-/Testbarkeit.** Eine Schemaversion, ein Migrationspfad. Isolationstests laufen gegen eine DB mit zwei Orgs (siehe Scaffolding-Plan, Abschnitt 7).

**Bewusst in Kauf genommen:** RLS erfordert Sorgfalt bei (a) der DB-Rolle, mit der die App verbindet (darf **nicht** `BYPASSRLS` haben, darf **nicht** Tabelleneigentümer/Superuser sein), (b) dem zuverlässigen Setzen des Tenant-Kontexts pro Transaktion und (c) Migrationen/Hintergrundjobs, die als privilegierte Rolle laufen und RLS bewusst umgehen. Diese Punkte werden unten als verbindliche Regeln festgehalten.

Die Detailbegründung und die verworfenen Alternativen sind in [adr/0001-mandantentrennung-rls.md](adr/0001-mandantentrennung-rls.md) festgehalten.

### 2.3 Wie I1 technisch *erzwungen* wird (drei Ebenen)

Die Invariante darf nicht versehentlich umgehbar sein. Sie wird auf drei sich ergänzenden Ebenen durchgesetzt:

**Ebene 1 — DB-Row-Level-Security (die nicht umgehbare Linie).**

- Jede tenant-behaftete Tabelle erhält `organization_id UUID NOT NULL` mit FK auf `organization(id)`.
- Auf jeder dieser Tabellen ist RLS **aktiviert und erzwungen**: `ALTER TABLE … ENABLE ROW LEVEL SECURITY; ALTER TABLE … FORCE ROW LEVEL SECURITY;` (`FORCE`, damit RLS auch für den Tabelleneigentümer gilt).
- Eine `USING`-Policy für Lesen und eine `WITH CHECK`-Policy für Schreiben binden jede Zeile an die aktive Organization, die als Session-Variable (GUC) gesetzt ist:

  ```sql
  -- Konzept (Implementierung in M0 durch bulk-impl, hier nur die Form):
  CREATE POLICY org_isolation ON <tabelle>
    USING      (organization_id = current_setting('app.current_org_id')::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid);
  ```

  Die `USING`-Klausel verhindert das *Lesen* fremder Zeilen; die `WITH CHECK`-Klausel verhindert das *Schreiben/Verschieben* einer Zeile in eine fremde Organization (z. B. ein INSERT/UPDATE, das ein fremdes `organization_id` setzen will).
- Die Anwendung verbindet mit einer DB-Rolle **ohne** `BYPASSRLS` und **ohne** Tabelleneigentum. Migrationen und Wartungsjobs verwenden eine getrennte, privilegierte Rolle.

**Ebene 2 — Tenant-Context-Middleware (setzt den Kontext zuverlässig).**

- Bei jedem authentifizierten Request leitet die Middleware die `organization_id` **ausschließlich aus der serverseitigen Session/dem geprüften Token** ab — niemals aus einem client-gelieferten Parameter, Header oder Body.
- Pro Request/Transaktion wird `app.current_org_id` über `SET LOCAL` (transaktionsgebunden) bzw. `set_config(..., is_local => true)` gesetzt, **bevor** die erste Tenant-Query läuft. `SET LOCAL` ist transaktionslokal und wird beim Commit/Rollback automatisch zurückgesetzt — entscheidend bei gepoolten Verbindungen, damit kein Kontext zwischen Requests „durchsickert".
- **Fail-closed:** Ist kein Tenant-Kontext gesetzt, liefert die RLS-Policy null Zeilen (die GUC ist leer → Vergleich schlägt fehl). Wir setzen die GUC zusätzlich per Default auf einen unmöglichen Wert, sodass „Kontext vergessen" zu „nichts sichtbar" führt, nie zu „alles sichtbar".

**Ebene 3 — Repository-/Query-Layer-Guard (frühe, klare Fehler + Performance).**

- Es gibt **keinen** direkten Tabellenzugriff aus Domain-Services; aller Tenant-Datenzugriff geht durch eine schmale Repository-/Data-Access-Schicht, die nur über den Org-gebundenen Session-Kontext arbeitet.
- Zusätzlich (nicht statt RLS, sondern für Performance und sprechende Fehler) filtert das Repository explizit auf `organization_id`. RLS ist das Sicherheitsnetz; der explizite Filter sorgt für sinnvolle Indexnutzung und macht im Code sichtbar, dass es eine Tenant-Query ist.
- Ein **Lint-/Review-Gate** (CI): tenant-behaftete Tabellen werden in einer zentralen Liste geführt; ein Test prüft, dass für jede dieser Tabellen RLS aktiviert ist und eine Policy existiert (siehe Abschnitt 7, Migrationstest). So kann eine neu hinzugefügte Tabelle nicht versehentlich ohne RLS in Produktion gelangen.

### 2.4 Warum drei Ebenen statt nur RLS

RLS allein schützt vor Datenleck, gibt aber bei fehlendem Kontext „still" null Zeilen zurück — das kann Bugs verschleiern. Der Repository-Guard macht solche Fälle früh und laut sichtbar. Die Middleware garantiert, dass der Kontext überhaupt zuverlässig gesetzt ist. Zusammen: **RLS verhindert das Leck, die anderen beiden Ebenen verhindern stille Fehlfunktion.**

### 2.5 Nicht-tenant-Tabellen (bewusste Ausnahmen)

Wenige Tabellen sind **global/systemweit** und tragen kein `organization_id`:

- `organization` selbst (die Tenant-Wurzel).
- Globale Systemstammdaten wie der **Standardkontenplan** (`cost_category`), sofern er für alle Orgs identisch ist (SDD §7 — fester Plan in Phase 1). Diese Tabelle ist lesbar für alle, schreibbar nur durch Migrationen/Systemrolle. *(Annahme A-1, siehe Abschnitt 8 — ggf. wird der Kontenplan pro-Org kopiert, falls Orgs ihn versionieren dürfen.)*
- Reine Auth-Infrastruktur, die einer Organization zugeordnet ist (`user`, `membership`), trägt `organization_id` und ist damit tenant-behaftet — siehe Abschnitt 4.

Jede Ausnahme ist explizit in der zentralen Tabellenliste vermerkt (Abschnitt 7), damit „kein `organization_id`" immer eine bewusste, geprüfte Entscheidung ist und nie ein Versehen.

### 2.6 Abgrenzung Mandantentrennung vs. Vermögenstrennung

Das sind **zwei verschiedene Isolationsachsen**, und die Unterscheidung ist fachlich wichtig (SDD §4):

- **Mandantentrennung (I1):** zwischen *Organizations*. Verwaltung A sieht nie Daten von Verwaltung B. → In M0 hart per RLS erzwungen.
- **Vermögenstrennung:** *innerhalb* einer Organization zwischen Mandaten/WEGs. Gelder und Belege der WEG „Hauptstr. 5" werden nicht mit denen der WEG „Bahnhofstr. 12" vermischt (Treuhand-/Fremdgeldtrennung).

Vermögenstrennung wird in M0 **nicht** als zweite RLS-Achse implementiert (das wäre verfrüht und träfe erst Konten/Zahlungen ab M6). Stattdessen wird sie **im Datenmodell vorbereitet**: `BankAccount`, `CostEntry`, `Payment`, `SettlementRun` etc. tragen einen Bezug auf `management_mandate` bzw. `property`, sodass die Trennung später sauber durchgesetzt und ausgewertet werden kann. Die Roadmap verankert Vermögenstrennung erst ab M6 voll — das Modell hält die Tür ab M0 offen.

> **[ENTSCHEIDUNG] / offener Punkt:** Soll Vermögenstrennung mittelfristig ebenfalls DB-seitig (zweite RLS-Achse oder Constraint-Ebene) erzwungen werden, oder genügt App-Layer-Durchsetzung über die Mandats-/Objektzuordnung? Empfehlung: in M0 nur Modell-Vorbereitung; Entscheidung über DB-Härtung mit M6 treffen, wenn Konten/Zahlungen real sind. Hier festhalten, damit es nicht vergessen wird.

---

## 3. GoBD-Unveränderbarkeit (I2) und Verzahnung mit der DSGVO-Löschpflicht

> Hinweis zur Tiefe: M0 verankert das Konzept im Datenmodell. Die *vertiefte* GoBD-Belegspeicher-Spezifikation ist als eigener Schritt in M2 vorgesehen (`engine-correctness`, Roadmap-Modellzuordnung). Dieser Abschnitt legt die tragende Struktur fest, damit M1/M2 darauf aufsetzen können.

### 3.1 Was unveränderbar sein muss (SDD §6.6, §14, §16)

1. **Freigegebene Belege** (`InvoiceDocument`: PDF und/oder E-Rechnungs-XML) — ab Freigabe nicht mehr überschreibbar.
2. **Finalisierte Abrechnungen** (`SettlementRun` und seine `SettlementLine`, ebenso finalisierte `EconomicPlan`, `AssetReport`) — als unveränderbare Snapshots, damit spätere Stammdatenänderungen alte Dokumente nicht verändern.
3. **Audit-Trail** (wer/wann/was) zu Belegen, Kostenpositionen und Freigaben.

### 3.2 Bausteine der Unveränderbarkeit

**(a) Append-only statt Überschreiben.**

- Belege und Snapshots werden **nie** per `UPDATE` inhaltlich geändert. Eine Korrektur erzeugt eine **neue Version** mit Bezug auf das Original (`supersedes_id` → vorige Version; das Original wird als `superseded` markiert, bleibt aber bestehen). So entsteht eine lückenlose Versionskette.
- Status-Übergänge (z. B. `draft_extracted → needs_review → confirmed → rejected`, SDD §9) sind erlaubt, solange der *finalisierte* Inhalt unangetastet bleibt. Ab `confirmed`/Festschreibung ist der inhaltstragende Teil eingefroren.

**(b) Integrität per SHA-256.**

- Beim Festschreiben eines Belegs wird über den Dateiinhalt (PDF-Bytes bzw. XML-Bytes) ein **SHA-256-Hash** berechnet und in `invoice_document.content_sha256` gespeichert.
- Der physische Inhalt liegt im Objektspeicher (M0: lokales FS mit Prüfsummen; später S3 mit Object-Lock/WORM + Versionierung, SDD §16). Die DB hält Metadaten, Hash und Speicherort/Version.
- Für **Snapshots** (Abrechnungen) wird der serialisierte Snapshot-Payload (kanonische JSON-Repräsentation aller berechneten Werte) ebenfalls gehasht (`settlement_run.snapshot_sha256`), sodass nachträgliche Manipulation erkennbar ist.

**(c) Versionierung mit Bezug zum Original.**

- Versionskette über `version` (laufend) + `supersedes_id` (FK auf Vorgänger) + Status `active`/`superseded`. Pro logischem Beleg ist zu jedem Zeitpunkt genau eine `active`-Version definiert.

**(d) Audit-Trail (append-only Event-Log).**

- Eine zentrale, append-only Tabelle `audit_event` protokolliert sicherheits-/abrechnungsrelevante Vorgänge: `actor_user_id`, `organization_id`, `entity_type`, `entity_id`, `action` (z. B. `document.released`, `settlement.finalized`, `user.role_changed`), `occurred_at`, `payload_diff` (JSON), optional `prev_event_hash` für eine verkettete Integritätssicherung (Hash-Chain — verhindert unbemerktes Löschen einzelner Audit-Zeilen).
- `audit_event` ist tenant-behaftet (trägt `organization_id`, RLS aktiv) und wird **nur via INSERT** geschrieben; kein UPDATE/DELETE im normalen Betrieb (durch fehlende Grants und CI-Test abgesichert).

**(e) Erzwingung gegen versehentliche Mutation.**

- Die Unveränderbarkeit wird nicht nur per Konvention, sondern DB-nah abgesichert: Für finalisierte/festgeschriebene Zeilen verhindert ein **BEFORE UPDATE/DELETE-Trigger** (bzw. entzogene UPDATE/DELETE-Grants auf den betreffenden Tabellen für die App-Rolle) jede Mutation der inhaltstragenden Spalten. Die konkrete Mechanik (Trigger vs. Grants vs. separate „frozen"-Tabelle) wird im M2-GoBD-Design final gewählt; M0 hält die Spalten und Statusfelder dafür bereit.

### 3.3 Aufbewahrung (8 Jahre) vs. DSGVO-Löschpflicht — das Spannungsfeld

SDD §6.6/§15 und §17 fordern beides: **mindestens 8 Jahre revisionssichere Belegaufbewahrung** (BEG IV ab 2025) *und* DSGVO-konforme Lösch-/Auskunftsfähigkeit, „ohne die revisionssichere Aufbewahrung zu verletzen". Das ist nur auflösbar, wenn man **Belegsubstanz** und **personenbezogene Stammdaten** sauber trennt.

**Konzept (M0 legt die Struktur, M9 härtet aus):**

1. **Trennung von Beleg und Person.** Ein Beleg (`InvoiceDocument`, Buchungsbeleg, finalisierte Abrechnung) ist als steuerlich relevanter Nachweis 8 Jahre aufzubewahren — *inhaltlich unverändert*. Personenbezogene Stammdaten (`Person`, Kontaktdaten, Bankdaten) liegen in eigenen Entitäten und werden referenziert, nicht in den Beleg hineinkopiert, soweit fachlich vertretbar. *(Grenze: Eine Rechnung/Abrechnung enthält naturgemäß Namen/Anschrift des Adressaten — dieser Teil ist Bestandteil des aufbewahrungspflichtigen Belegs und wird **nicht** gelöscht, sondern fällt unter die gesetzliche Aufbewahrung als Rechtsgrundlage gegen den Löschanspruch.)*
2. **Krypto-Löschung / Anonymisierung statt Hard-Delete.** Auf eine Löschanfrage hin werden personenbezogene Daten, die *nicht* Teil eines aufbewahrungspflichtigen Belegs sind, gelöscht bzw. anonymisiert; aufbewahrungspflichtige Belege bleiben unverändert, werden aber gegen weitere Verarbeitung **gesperrt** (Verarbeitungseinschränkung statt Löschung — die DSGVO erlaubt Aufbewahrung bei gesetzlicher Aufbewahrungspflicht und sieht dann Einschränkung der Verarbeitung vor).
3. **Lösch-/Sperr-Status im Modell.** `Person` (und verwandte Stammdaten) erhalten ab M0 die Felder `deletion_requested_at`, `processing_restricted` (bool), `anonymized_at`. So ist das Konzept ab Tag 1 im Modell verankert; die eigentliche Lösch-/Anonymisierungs-Mechanik kommt in M9.
4. **Aufbewahrungsanker.** Belege/Snapshots erhalten ein `retention_until`-Feld (berechnet aus dem maßgeblichen Datum + 8 Jahre — *welches Datum genau maßgeblich ist, ist offener Punkt, siehe unten*), damit der Löschprozess automatisiert prüfen kann, was gelöscht werden darf und was gesperrt bleiben muss.

> **[ENTSCHEIDUNG] / SDD §18 (Detailtiefe Lösch-/Anonymisierungskonzept):** Dieses Spannungsfeld ist in SDD §18 explizit als offen markiert. **Es blockiert M0/M1 nicht** (die Felder werden vorgesehen, die Mechanik kommt in M9), muss aber vor M9 fachlich geklärt werden, idealerweise mit Steuerberater/Datenschutzbeauftragtem:
> - Welches Datum startet die 8-Jahres-Frist je Belegart (Rechnungsdatum, Ende des Wirtschaftsjahres, Festschreibedatum)?
> - Welche personenbezogenen Felder gelten als „Belegbestandteil" (aufbewahrungspflichtig) vs. „reine Stammdaten" (löschbar)?
> - Anonymisierung vs. Sperrung: technische Ausgestaltung (z. B. Pseudonymisierung der Stammdaten bei erhaltener Belegreferenz).
>
> **Keine Selbst-Zertifizierung:** Dieses Design macht die Aufbewahrung transparent und auditierbar; ob es im Einzelfall gerichts-/prüfungsfest ist, ist fachlich (Steuerberater/Fachanwalt) abzunehmen.

---

## 4. Auth & Rollen

### 4.1 Entitäten

- **`Organization`** — Mandanten-/Isolationswurzel (SDD §4). Trägt kein `organization_id` (sie *ist* der Tenant).
- **`User`** — ein Anmeldekonto. **Annahme A-2:** Ein User gehört in Phase 1 zu **genau einer** Organization (`user.organization_id`). Das vereinfacht Tenant-Context und RLS erheblich. *(Falls ein Dienstleister-Account später mehrere Verwaltungen bedienen soll, wäre eine n:m-`Membership` nötig — bewusst out-of-scope für M0, siehe offener Punkt unten.)*
- **`Membership` / Rollenzuordnung** — verbindet `User` mit `Organization` und einer **Rolle**. Bei A-2 (ein User = eine Org) kann die Rolle auch direkt am User hängen; ich modelliere sie dennoch als eigene Zuordnung (`membership`), um die spätere n:m-Erweiterung nicht zu verbauen. *(Designvorschlag, nicht hart erforderlich.)*
- **`Role`** — feste Aufzählung, **nicht** frei konfigurierbar (SDD §4): `OWNER_ADMIN` (Inhaber/Admin), `CLERK` (Sachbearbeiter), `READ_ONLY` (Nur-Lesen). Als Enum/feste Stammdaten, nicht als editierbare Tabelle mit Einzelrechten (SDD §3 Nicht-Ziel: kein feingranulares Rechtesystem).
- **`ManagementMandate`** — geschäftliche Beauftragung *innerhalb* einer Organization (SDD §4), mit Typ (`WEG`, `MIET`, `SEV`) und Laufzeit (`start_date`, `end_date`). Trägt `organization_id` (tenant-behaftet). Verbindet die Verwaltung mit einem verwalteten Kontext und ist der Anker für die spätere Vermögenstrennung.

### 4.2 Rollenmatrix (aus SDD §4, für M0 konkretisiert)

| Aktion (M0-relevant) | OWNER_ADMIN | CLERK | READ_ONLY |
| --- | --- | --- | --- |
| Organization-Einstellungen, Nutzer-/Rollenverwaltung | ✔ | ✗ | ✗ |
| Mandate anlegen/ändern | ✔ | (lesen) | (lesen) |
| Operative Erfassung/Abrechnung/Belegfreigabe (ab M2+) | ✔ | ✔ | ✗ |
| Lesen aller Org-Daten | ✔ | ✔ | ✔ |

(`✗` = kein Zugriff; READ_ONLY ist konsequent lesend, z. B. für Beirat/Wirtschaftsprüfer/Vertretung.)

Die Rollendurchsetzung erfolgt im Authorization-Layer (Policy-Check pro Endpunkt). Sie ist **orthogonal zur Mandantentrennung**: RLS stellt sicher, *welche* Organization sichtbar ist; RBAC stellt sicher, *was* ein User innerhalb seiner Organization darf. Beide greifen unabhängig.

### 4.3 Login/Session-Ansatz *(Empfehlung, [ENTSCHEIDUNG])*

- **Session-Modell:** Server-seitige Sessions mit HTTP-only, Secure, SameSite-Cookies (kein Token im JS-Zugriff → XSS-Härtung). Alternativ kurzlebige JWT-Access-Tokens + Refresh; für ein Verwaltungs-Backoffice mit Browser-UI ist die **Server-Session der robustere Default** (einfacher Logout/Invalidierung, keine Token-Leak-Fläche).
- **Passwort-Hashing:** Argon2id (oder bcrypt als Fallback), nie Klartext, nie reversibel.
- **Tenant-Bindung:** Die `organization_id` des Users steckt in der serverseitigen Session und ist die *einzige* Quelle für den Tenant-Context (Abschnitt 2.3, Ebene 2). Kein Org-Switch über Request-Parameter.
- **MFA:** für M0 nicht zwingend; als Härtungsoption für M9 vormerken (Backoffice mit personenbezogenen + finanzrelevanten Daten).
- **Audit:** Login, Logout, fehlgeschlagene Logins, Rollenänderungen → `audit_event`.

> **[ENTSCHEIDUNG]:** Server-Session (empfohlen) vs. JWT. Und: Self-Hosting-Auth vs. externer Identity-Provider (z. B. Keycloak/Authentik). Für das MVP empfehle ich **eigene, schlanke Server-Session-Auth** (volle Kontrolle, keine externe Abhängigkeit), die Schnittstelle aber so kapseln, dass ein späterer IdP-Wechsel möglich bleibt.

> **Offener Punkt (out-of-scope M0):** User in mehreren Organizations (n:m). In Phase 1 bewusst ausgeschlossen (A-2). Falls Verwaltungs-Verbünde oder geteilte Dienstleister-Accounts gebraucht werden, vor M-Entscheidung neu bewerten.

---

## 5. Datenmodell für M0 + M1

Dieser Abschnitt verfeinert die SDD-Entitäten (§11/§13) um (a) Tenant-Scoping, (b) temporale Felder, (c) Unveränderbarkeits-Felder. **M0 implementiert nur den Kern (Organization, User, Membership, ManagementMandate) plus das Migrations-/RLS-Fundament.** M1 fügt Stammdaten hinzu. Die übrigen Entitäten (Kosten, Belege, Abrechnung, …) sind hier nur als *Vorausschau* skizziert, soweit sie M0/M1-Strukturentscheidungen beeinflussen — sie werden in ihren Milestones detailliert.

### 5.1 Querschnitts-Konventionen (für **alle** Tabellen verbindlich)

| Konvention | Regel | Begründung |
| --- | --- | --- |
| **Primärschlüssel** | `id UUID` (Default `gen_random_uuid()`), nicht fortlaufende Integer für fachliche Entitäten. | Keine Erratbarkeit/Enumeration über Org-Grenzen; verteilbar. |
| **Tenant-Spalte** | `organization_id UUID NOT NULL` (FK → `organization.id`) auf **jeder** tenant-behafteten Tabelle. Ausnahmen nur lt. Abschnitt 2.5. | I1 (Mandantentrennung). |
| **Geldbeträge (I4)** | **`NUMERIC(14,2)`** für EUR-Beträge — **niemals** `float`/`double`/`real`. Vorzeichen erlaubt (Nachzahlung/Guthaben). | Exakte Dezimalrechnung; Gleitkomma verbietet sich für Geld (Rundungsfehler, nicht prüffest). |
| **Quoten/Schlüssel** | MEA und Verteilungsanteile als **`NUMERIC`** mit ausreichender Skala (z. B. `NUMERIC(12,6)` für Anteile/Zähler-Nenner) — exakt, nicht gerundet gespeichert; Rundung erst beim finalen Ausweis (Regel kommt mit M3). | Abrechnungssicherheit; Rundung ist eine bewusste, dokumentierte Operation, kein Nebeneffekt von Gleitkomma. |
| **Zeitstempel** | `created_at`, `updated_at` als `TIMESTAMPTZ` (UTC). | Nachvollziehbarkeit, Zeitzonensicherheit. |
| **Temporale Gültigkeit** | Historien-Tabellen: `valid_from DATE NOT NULL`, `valid_to DATE NULL` (NULL = offen). | I3 (chronologische Integrität); anteilige Zeiträume. |
| **Soft-/Append-Semantik** | Keine Hard-Deletes auf historien- oder belegtragenden Tabellen; Statusfelder statt Löschung. | I2/I3 und DSGVO-Sperrkonzept (Abschnitt 3.3). |
| **Geld-Genauigkeit Hinweis** | Anwendungsseitig konsequent `Decimal`/dezimaler Typ; keine impliziten Float-Konvertierungen (siehe Stack, Abschnitt 6). | I4 durchgängig vom Speicher bis zur Berechnung. |

### 5.2 ER-Übersicht (M0-Kern, fett = in M0 angelegt)

```
                         ┌────────────────────────┐
                         │ organization            │  (Tenant-Wurzel, KEIN org_id)
                         │ - id PK (UUID)           │
                         │ - name                   │
                         │ - created_at             │
                         └───────────┬──────────────┘
            ┌────────────────────────┼───────────────────────────┐
            ▼                        ▼                            ▼
 ┌───────────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐
 │ user               │   │ management_mandate     │   │ (M1: property, person …) │
 │ - id PK            │   │ - id PK                │   │   alle mit org_id, RLS   │
 │ - organization_id  │FK │ - organization_id   FK │   └─────────────────────────┘
 │ - email (uniq/org) │   │ - mandate_type         │
 │ - password_hash    │   │   (WEG|MIET|SEV)       │
 │ - status           │   │ - start_date           │
 │ - created_at       │   │ - end_date  (NULL=offen)│
 └─────────┬──────────┘   │ - created_at           │
           │              └───────────┬────────────┘
           ▼                          │ (M1: mandate ↔ property/unit-Bezug)
 ┌───────────────────┐                ▼
 │ membership         │     ┌──────────────────────────┐
 │ - id PK            │     │ audit_event  (append-only) │
 │ - organization_id  │FK   │ - id PK                    │
 │ - user_id       FK │     │ - organization_id       FK │
 │ - role (ENUM:      │     │ - actor_user_id         FK │
 │   OWNER_ADMIN|     │     │ - entity_type / entity_id  │
 │   CLERK|READ_ONLY) │     │ - action                   │
 │ - created_at       │     │ - occurred_at              │
 └───────────────────┘     │ - payload_diff (JSONB)     │
                           │ - prev_event_hash (opt.)   │
                           └──────────────────────────┘
```

**Constraints (M0-Kern):**
- `user`: `UNIQUE (organization_id, email)` — E-Mail eindeutig je Organization (nicht global, da A-2 ein User = eine Org; verschiedene Orgs könnten dieselbe Verwalter-E-Mail-Domäne nutzen). RLS aktiv.
- `membership`: `UNIQUE (organization_id, user_id)` bei A-2 (eine Rolle je User je Org); FK auf `user` und `organization`. RLS aktiv.
- `management_mandate`: `CHECK (end_date IS NULL OR end_date >= start_date)`; `mandate_type` als Enum. RLS aktiv.
- `audit_event`: nur INSERT-Grant für App-Rolle; RLS aktiv; `occurred_at` server-gesetzt.
- **RLS auf `user`, `membership`, `management_mandate`, `audit_event`** aktiviert + `FORCE`.

### 5.3 ER-Übersicht (M1-Stammdaten — Design jetzt, Implementierung in M1)

```
 organization 1───* property 1───* unit
                       │             │
                       │             ├──* ownership_history   (temporal: valid_from/valid_to)
                       │             │       └─ owner_profile  (FK → person)
                       │             └──* lease_agreement      (temporal: valid_from/valid_to)
                       │                     └─ tenant_profile (FK → person)
                       │
                       ├──* bank_account        (Vermögenstrennung; FK → mandate/property)
                       └──* meter 1───* meter_reading

 person (Basis-Stammdaten, DSGVO-Felder)
   ├─ owner_profile   (1:1 Rolle als Eigentümer)
   ├─ tenant_profile  (1:1 Rolle als Mieter)
   └─ vendor          (Dienstleister/Lieferant; eigene Entität oder person-Spezialisierung)
```

**`property`** (Objekt, SDD §6.1): `organization_id` FK, `name`, Adresse, **`management_type`** (`MIET`|`WEG`|`SEV`|`MISCH`), Abrechnungszeitraum-Konvention. RLS aktiv.

**`unit`** (Einheit, SDD §6.1): `organization_id`, `property_id` FK, `number`, `location`, **`area NUMERIC(10,2)`** (Wohn-/Nutzfläche), **`heated_area NUMERIC(10,2)`** (beheizte Fläche), `usage_type` (Wohnung|Gewerbe|Stellplatz|Sonstige), **`mea NUMERIC(12,6)`** (Miteigentumsanteil — exakt, nicht Float). Optionale Verteilungsbesonderheiten. RLS aktiv.

**`person`** (SDD §6.2): `organization_id`, Kontaktdaten, optional Bankdaten. **DSGVO-Felder (Abschnitt 3.3):** `deletion_requested_at`, `processing_restricted`, `anonymized_at`. RLS aktiv. Eigentümer/Mieter/Vendor als getrennte Logiken (SDD §6.2) — entweder Spezialisierungen von `person` (`owner_profile`, `tenant_profile`, `vendor`) oder eigene Tabellen mit Person-Bezug; **[ENTSCHEIDUNG] für M1**, hier als 1:1-Profile vorgeschlagen, da eine Person mehrere Rollen haben kann (z. B. Eigentümer *und* selbst Mieter).

**`ownership_history`** (SDD §6.2, §14 — *Kern der chronologischen Integrität*): `organization_id`, `unit_id` FK, `owner_profile_id` FK, **`valid_from DATE NOT NULL`**, **`valid_to DATE NULL`**, **`mea_share NUMERIC(12,6)`** (Anteil dieses Eigentümers an der Einheit, falls geteiltes Eigentum). RLS aktiv.
- **Constraint für Überschneidungsfreiheit:** Pro `unit_id` dürfen sich die `[valid_from, valid_to)`-Intervalle eines Eigentumsstrangs **nicht überlappen**. Empfohlen über eine PostgreSQL **EXCLUDE-Constraint** mit `daterange` und `&&`-Operator (btree_gist), die Org- und Unit-Bezug einschließt. Das ist die DB-seitige Garantie für das M1-Exit-Kriterium („lückenlos und überschneidungsfrei"). *(Genaue Form — ob pro Einheit ein einziger Eigentümer-Strang oder mehrere Bruchteilseigentümer mit Summenbedingung der Anteile — ist eine M1-Detailfrage; die EXCLUDE-Mechanik wird hier als Mittel festgehalten.)*

**`lease_agreement`** (SDD §6.2, §14): `organization_id`, `unit_id` FK, `tenant_profile_id` FK, **`valid_from`/`valid_to`** (Vertragsbeginn/-ende), **`base_rent NUMERIC(14,2)`** (Kaltmiete), **`advance_payment NUMERIC(14,2)`** (NK-Vorauszahlung), optional **`deposit NUMERIC(14,2)`** (Kaution), Notizen. RLS aktiv. Gleiche **EXCLUDE-Constraint**-Logik gegen überlappende Mietzeiträume je Einheit (mit Blick auf Leerstand: Lücken sind *erlaubt* — Leerstand —, Überlappungen nicht).

**`bank_account`** (SDD §6.3 — Vermögenstrennung): `organization_id`, Bezug auf `management_mandate`/`property`, `account_type` (laufend|Rücklage|Kaution), IBAN/Bank-Stammdaten. RLS aktiv. *(Implementierung erst ab M6; Modell-Anker in M1 vorbereiten, damit Mandat/Objekt-Bezug konsistent ist.)*

**`meter` / `meter_reading`** (SDD §6.2.1): optionale Objekt-/Einheitszähler mit Ablesungen (`reading_date`, `value NUMERIC(14,4)`, `period`). RLS und explizite Org-Filter aktiv. Seit T29 bietet `/api/meters` GET/POST, `/{id}` GET/PATCH für Beschreibungsfelder und `/{id}/readings` GET/POST für unverändert erhaltene Ablesungen. Repository und API prüfen sichtbares Elternobjekt, Einheitskohärenz und bestehende Master-Lese-/Prepare-Rechte; keine nachträgliche Umhängung. Schreiboperationen halten Live-ACL-/Finanz-/Zeilensperren in dieser Reihenfolge und committen vor der Erfolgsantwort. Messwerte werden ohne Float oder Rundung validiert und als vierstellige Dezimalstrings ausgegeben. Seit T30 verwenden Objekt- und Einheitsdetail dasselbe `MeterPanel` mit festem Kontext und Rollenflag; String-/BigInt-Rechnung zeigt die Differenz der letzten zwei eindeutigen Tage, nicht bereits eine T31-Abrechnungsvorbelegung. Kontextwechsel entkoppeln verspätete Antworten, Schreibvorgänge sind synchron gegen Mehrfachauslösung gesperrt; Fehler behalten die Eingaben.

### 5.4 Vorausschau auf I2-tragende Entitäten (Detail in M2/M3/M4)

Hier nur die Felder, die das Unveränderbarkeits-/Snapshot-Konzept im Modell verankern (vollständige Definition in den jeweiligen Milestones):

- **`cost_category`** (SDD §7): fester Standardkontenplan; **versionierte Systemstammdaten** (`version`, Gültigkeit), global oder pro-Org kopiert (Annahme A-1). Defaults für Umlagefähigkeit + Verteilerschlüssel.
- **`cost_entry`** (SDD §6.4): `organization_id`, `property_id`, optional `management_mandate_id`, `cost_category_id`, Lieferant (`vendor`), Rechnungsdatum, **Leistungszeitraum `service_period_from`/`_to`** (SDD §14 — nicht nur Rechnungsdatum!), **`gross_amount NUMERIC(14,2)`**, optional USt-Betrag/-Satz (`NUMERIC`), Belegnummer, getrennte Verteilerschlüssel für Eigentümer-/Mieterabrechnung, Umlagefähigkeit, Status der Belegherkunft/Freigabe (`draft_extracted|needs_review|confirmed|rejected`).
- **`invoice_document`** (SDD §6.6, §13): `organization_id`, Bezug auf `cost_entry`, `storage_uri`, **`content_sha256`**, **`version`**, **`supersedes_id`** (FK self), `status` (`active|superseded`), `released_at`, `released_by`, **`retention_until`** (Aufbewahrungsanker, Abschnitt 3.3).
- **`settlement_run`** (SDD §6.9, §13, §16): `organization_id`, `property_id`/`mandate`, Typ (BK|WEG-Jahresabrechnung|Wirtschaftsplan|Vermögensbericht), Abrechnungszeitraum, `status` (`draft|finalized`), **`snapshot_payload JSONB`** (eingefrorene Berechnungsbasis), **`snapshot_sha256`**, `finalized_at`, `finalized_by`. Ab `finalized` unveränderbar (Trigger/Grants, Abschnitt 3.2e).
- **`settlement_line`**: pro Adressat/Kategorie; gehört zum Snapshot des `settlement_run`; alle Beträge `NUMERIC(14,2)`.
- Audit-Trail (`audit_event`) — bereits in M0 (Abschnitt 5.2), wird ab M2 intensiv genutzt.

> Diese Vorausschau ist **nicht** Teil der M0-Implementierung. Sie steht hier, damit die M0-Strukturentscheidungen (UUID-PKs, `organization_id`, `NUMERIC`, Status-/Versionsfelder, RLS-Tabellenliste) konsistent zur späteren Modellfamilie sind und `bulk-impl` nicht in M2 umbauen muss.

---

## 6. Stack-Empfehlung *(Empfehlung — der Mensch muss bestätigen, noch nichts committen)*

Die SDD (§16) lässt das Backend offen: **Python/FastAPI** vs. **Node.js/NestJS**. Bewertung entlang der Anforderungen genau dieser Domäne:

| Kriterium (domänenspezifisch) | Python / FastAPI | Node.js / NestJS | Gewicht |
| --- | --- | --- | --- |
| **Exakte Dezimalrechnung** (I4, Kern der Abrechnung) | `decimal.Decimal` ist erstklassiger Standard-Datentyp; ORM-Mapping `NUMERIC ↔ Decimal` verlustfrei und idiomatisch. Sehr gut. | JS-`number` ist IEEE-754-Float → für Geld ungeeignet; man **muss** auf eine Lib (decimal.js / big.js) ausweichen und Disziplin erzwingen, dass *nirgends* ein `number` für Geld durchrutscht. Machbar, aber fehleranfälliger. | **hoch** |
| **Abrechnungs-Engine** (komplexe, testgetriebene Geschäftslogik, M3/M4) | Klar, gut testbar (pytest); reichhaltige numerische/datumsbezogene Standardbibliothek. | Gut testbar (Jest); funktioniert ebenfalls. | hoch |
| **XRechnung/ZUGFeRD-Parsing-Ökosystem** (M5) | Reifes Ökosystem für XML/EN-16931, PDF/A-3-Extraktion (eingebettetes XML), lxml-basiertes Schema-/Schematron-Handling. | Ökosystem vorhanden, aber für EN-16931/ZUGFeRD tendenziell weniger Out-of-the-box-Tiefe. | mittel |
| **PDF-Erzeugung** (HTML→PDF, SDD §16) | Anbindung an Playwright (HTML→PDF) sprachunabhängig; serverseitige PDF-Libs vorhanden. | Playwright/Puppeteer sind hier nativ zuhause (JS). Leichter Vorteil. | mittel |
| **Migrations-Tooling** | Alembic (mit SQLAlchemy) — sehr ausgereift, fein steuerbar; **RLS-Policies/Trigger lassen sich als explizite Migrations-Schritte sauber versionieren**. | Migrationen (TypeORM/Prisma) gut; Prisma kann bei RLS/rohen Policy-DDL und Session-GUCs etwas mehr Handarbeit/Escape-Hatches erfordern. | **hoch** (RLS!) |
| **Testbarkeit** (Korrektheit ist das Produkt) | pytest + Hypothesis (Property-based Testing für Verteilungs-/Rundungslogik) — stark für genau diese Domäne. | Jest + fast-check möglich; gleichwertig nutzbar. | hoch |
| **OCR/KI-Anbindung** (M2 PDF-Extraktion) | Python ist die natürliche Sprache für OCR/ML-Anbindung und lokale Modelle. | Über Services anbindbar. | niedrig–mittel |
| **Team/Konsistenz mit Frontend** | Zwei Sprachen (TS-Frontend, Python-Backend) — Kontextwechsel. | Eine Sprache (TS überall) — geteilte Typen/DTOs möglich. **Vorteil Node.** | mittel |

**Empfehlung: Python / FastAPI** für das Backend, mit React/TypeScript-Frontend (Frontend ist durch SDD §16 ohnehin festgelegt).

**Kurzbegründung:** Der wertbestimmende und risikoreichste Teil dieses Produkts ist die **korrekte, prüffeste Abrechnung mit exakter Dezimalrechnung** (M3 ist laut Roadmap der größte, riskanteste Brocken). Python liefert hier den geringsten „Reibungswiderstand": `Decimal` ist erste Klasse (kein dauerhaftes Float-Vermeidungs-Regime nötig), Alembic versioniert **RLS-Policies und Trigger als saubere, reviewbare Migrationsschritte** (zentral für die Erzwingung von I1/I2), und das XRechnung/EN-16931-Ökosystem (M5) ist reifer. Der Hauptnachteil — zwei Sprachen im Stack statt einer — wiegt für ein korrektheitskritisches Finanz-/Rechtsprodukt weniger schwer als die durchgängige Geld-/Dezimalsicherheit und das RLS-/Migrations-Handling.

> **[ENTSCHEIDUNG]:** Backend Python/FastAPI (empfohlen) vs. Node/NestJS. Sekundär, falls FastAPI gewählt wird: ORM/Migrations-Kombi **SQLAlchemy + Alembic** (empfohlen wegen feiner RLS-/DDL-Kontrolle) vs. eine higher-level-Alternative. **Bitte bestätigen, bevor `bulk-impl` scaffoldet — es wird noch nichts committet.**

> **Wichtig (unabhängig vom Backend):** Was auch gewählt wird, diese Punkte sind nicht verhandelbar: (1) Geld als dezimaler Typ, nie Float; (2) RLS + `FORCE` + App-Rolle ohne `BYPASSRLS`; (3) transaktionslokales Setzen des Tenant-Kontexts (`SET LOCAL`/`set_config(... is_local => true)`); (4) Migrationen mit explizit versionierten RLS-Policies/Triggern.

---

## 7. Scaffolding-Plan für `bulk-impl` (M0-Umsetzung)

Ziel: M0-Exit erfüllen — „Zwei Organizations können angelegt werden und sehen garantiert keine Daten der jeweils anderen; Rollen greifen." Abarbeitbare Checkliste. **Voraussetzung: Stack-Bestätigung durch den Menschen (Abschnitt 6). Vorher kein Commit.**

> Diese Checkliste ist für `bulk-impl` (Opus 4.6, Bulk-Engineering) geschrieben. Architektur-/Datenmodell-Entscheidungen sind in diesem Dokument bereits getroffen; `bulk-impl` setzt sie um und eskaliert nur, wenn eine offene Spezifikationsfrage (SDD §18) oder eine Mandanten-/GoBD-Designfrage auftaucht (gemäß `bulk-impl.md` Eskalationsregel).

### 7.1 Projektstruktur (Vorschlag, FastAPI-Variante)

```
hausverwaltung/
├── docs/                       # SDD, roadmap, diese architecture.md (unangetastet lassen)
├── backend/
│   ├── app/
│   │   ├── main.py             # App-Bootstrap, API-first
│   │   ├── core/
│   │   │   ├── config.py       # Settings (DB-URL, Session-Secret, EU-Region)
│   │   │   ├── db.py           # Engine/Session; setzt app.current_org_id via SET LOCAL
│   │   │   ├── tenant.py       # Tenant-Context-Middleware (Org aus Session)
│   │   │   ├── security.py     # Passwort-Hashing (Argon2id), Session-Handling
│   │   │   └── rbac.py         # Rollen-Enum + Authorization-Dependency
│   │   ├── models/             # ORM-Modelle (Organization, User, Membership, Mandate, AuditEvent)
│   │   ├── repositories/       # Org-gebundener Datenzugriff (kein direkter Tabellenzugriff sonst)
│   │   ├── api/                # Router (auth, organizations, users, mandates) — API-first
│   │   └── services/           # Domain-Services (M0: Org/User/Mandate-Verwaltung)
│   ├── migrations/             # Alembic; RLS-Policies + Trigger als explizite Schritte
│   │   └── versions/
│   ├── tests/
│   │   ├── conftest.py         # Test-DB, zwei Test-Orgs, Org-gebundene Clients
│   │   ├── test_tenant_isolation.py   # DER Isolationstest (siehe 7.4)
│   │   ├── test_rls_coverage.py       # RLS auf jeder Tenant-Tabelle (siehe 7.3)
│   │   └── test_rbac.py               # Rollen greifen
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── app/                # Routing, Layout-Grundgerüst (Desktop + mobil)
│   │   ├── auth/               # Login-Flow, Session
│   │   └── shell/              # Navigations-/Layout-Shell (responsive)
│   └── package.json
├── infra/
│   ├── docker-compose.yml      # PostgreSQL (+ ggf. Playwright-Service als PDF-Pipeline-Skelett)
│   └── db/roles.sql            # App-Rolle OHNE BYPASSRLS; getrennte Migrations-Rolle
└── README.md
```

### 7.2 Migrations- und DB-Setup (Reihenfolge)

1. **DB-Rollen anlegen** (`infra/db/roles.sql`): eine **App-Rolle** (`app_rw`) ohne `BYPASSRLS`, ohne Tabelleneigentum, ohne Superuser; eine getrennte **Migrations-/Owner-Rolle** (`app_migrator`) für DDL. Die App verbindet ausschließlich als `app_rw`. *(Erzwingt: RLS gilt auch für die App; Migrationen können RLS bewusst umgehen.)*
2. **Erste Migration — Kerntabellen:** `organization` (ohne `organization_id`), `user`, `membership`, `management_mandate`, `audit_event` mit den Spalten/Constraints aus Abschnitt 5.2. UUID-PKs (`gen_random_uuid()`, ggf. `pgcrypto`/`pg_uuidv7`). Geldfelder existieren in M0 nur als Vorbereitung; falls keine in M0 → kein Float-Risiko, aber Konvention dokumentieren.
3. **Zweite Migration — RLS-Fundament (eigener, expliziter Schritt):** für jede Tenant-Tabelle (`user`, `membership`, `management_mandate`, `audit_event`):
   - `ALTER TABLE … ENABLE ROW LEVEL SECURITY;`
   - `ALTER TABLE … FORCE ROW LEVEL SECURITY;`
   - `CREATE POLICY org_isolation … USING (organization_id = current_setting('app.current_org_id')::uuid) WITH CHECK (…);`
   - Default-GUC so wählen, dass „nicht gesetzt" = „nichts sichtbar" (fail-closed).
   - `audit_event`: zusätzlich UPDATE/DELETE-Grants für `app_rw` **entziehen** (append-only).
4. **Tenant-Context-Verdrahtung:** `db.py`/`tenant.py` setzen pro Request-Transaktion `SET LOCAL app.current_org_id = '<uuid>'` (bzw. `select set_config('app.current_org_id', :org, true)`) aus der Session. Connection-Pool beachten: transaktionslokal, kein Leak zwischen Requests.
5. **Index-Begleitung:** Indizes auf `organization_id` (und zusammengesetzt mit häufigen Filterspalten), damit RLS-Filter performant sind.

### 7.3 Verbindlicher RLS-Coverage-Test (`test_rls_coverage.py`)

Ein automatisierter Test, der **versehentlich ungeschützte Tabellen verhindert** (Ebene-3-Gate aus Abschnitt 2.3):

- Pflege eine zentrale Liste `TENANT_TABLES` (tenant-behaftet) und `GLOBAL_TABLES` (bewusste Ausnahmen lt. Abschnitt 2.5).
- Der Test fragt `pg_class.relrowsecurity`/`relforcerowsecurity` und `pg_policies` ab und assertet: **jede** Tabelle in `TENANT_TABLES` hat RLS aktiviert **und** `FORCE` **und** mindestens eine Policy mit `USING`+`WITH CHECK`. Jede Tabelle im DB-Schema ist entweder in `TENANT_TABLES` oder in `GLOBAL_TABLES` — eine unbekannte Tabelle lässt den Test **failen** (zwingt zur bewussten Einordnung).

### 7.4 Der konkrete Isolationstest (`test_tenant_isolation.py`) — Kern des M0-Exits

Setup (Fixtures): Test-DB; **zwei Organizations** `org_a`, `org_b`; je ein User mit Rolle; je ein `management_mandate`; je eine Beispielzeile in mindestens einer Tenant-Tabelle. Zwei API-Clients, jeweils als User der jeweiligen Org eingeloggt (echter Login-Flow, echter Tenant-Context).

Mindest-Assertions:

1. **Lese-Isolation (API):** Client A listet Mandate/Users → sieht **nur** `org_a`-Daten, niemals `org_b`. Und umgekehrt.
2. **Direktzugriff auf fremde ID (API):** Client A ruft eine `org_b`-Ressource per bekannter UUID direkt ab → **404/Not Found** (nicht 403, um Existenz nicht zu verraten), kein Datenleck.
3. **Schreib-Isolation / WITH CHECK:** Versuch, eine Zeile mit fremder `organization_id` anzulegen oder eine eigene Zeile in `org_b` zu „verschieben" → von DB (WITH CHECK) **abgewiesen**.
4. **RLS ohne App-Filter (DB-Ebene, der entscheidende Test):** Direkt über eine `app_rw`-DB-Session mit gesetztem `app.current_org_id = org_a` ein **ungefiltertes** `SELECT * FROM management_mandate` absetzen → liefert **ausschließlich** `org_a`-Zeilen. Beweist, dass die Isolation auch dann hält, wenn der Anwendungs-Filter fehlt (Defense-in-Depth, Option B).
5. **Fail-closed ohne Kontext:** Session **ohne** gesetzten `app.current_org_id` → `SELECT` liefert **null Zeilen** (nicht „alles"). Beweist die fail-closed-Eigenschaft.
6. **RBAC greift (`test_rbac.py`):** `READ_ONLY` darf lesen, aber kein Anlegen/Ändern (→ 403); `CLERK` darf operativ, aber keine Nutzer-/Rollenverwaltung (→ 403); `OWNER_ADMIN` darf alles in seiner Org. Innerhalb der eigenen Org, kombiniert mit Isolation aus 1–5.

> Bestehen die Tests 1–6, ist das M0-Exit-Kriterium objektiv nachgewiesen: zwei Orgs, garantierte Datentrennung (auf API- **und** DB-Ebene), Rollen greifen.

### 7.5 App-Shell & Infrastruktur (parallel, Roadmap-M0)

- **Responsive Web-App-Shell** (React/TS): Navigations-/Layout-Grundgerüst (Desktop + mobil), Login-Seite, Org-Kontext-Anzeige, Platzhalter-Navigation für spätere Module. Keine native App.
- **Auth-Flow** end-to-end: Login → Session → Tenant-Context im Backend.
- **PDF-Rendering-Pipeline als Skelett:** HTML→PDF (Playwright) als Service/Stub aufsetzen und mit einem Smoke-Test (z. B. „Hello-PDF") verdrahten — fachlich leer, aber als Infrastruktur vorhanden (Roadmap M0). Eigentliche Abrechnungs-PDFs ab M3.
- **Objektspeicher-Skelett:** lokales FS mit Prüfsummen-Hilfsfunktion (SHA-256) als Stub; Schnittstelle so kapseln, dass später S3 mit Object-Lock/WORM dahinter kann (SDD §16). Fachliche Belegablage erst M2.

### 7.6 Definition of Done für M0 (Übergabe an die nächste Stufe)

- [ ] App-Rolle ohne `BYPASSRLS`; getrennte Migrations-Rolle; dokumentiert in `infra/db/roles.sql`.
- [ ] Migrationen: Kerntabellen + RLS-Policies (`ENABLE`+`FORCE`+`USING`/`WITH CHECK`) als explizite, reviewbare Alembic-Schritte.
- [ ] Tenant-Context wird transaktionslokal aus der Session gesetzt; nie aus Client-Input.
- [ ] `test_rls_coverage.py` grün (jede Tenant-Tabelle geschützt; keine unbekannte Tabelle).
- [ ] `test_tenant_isolation.py` Assertions 1–5 grün (inkl. DB-Ebene 4 und fail-closed 5).
- [ ] `test_rbac.py` grün (drei Rollen greifen).
- [ ] App-Shell mit Login + Org-Kontext; PDF- und Objektspeicher-Skelett vorhanden.
- [ ] Geld-/Dezimal-Konvention dokumentiert (`NUMERIC`, app-seitig `Decimal`), auch wenn in M0 noch wenig Geld vorkommt.
- [ ] Keine offene SDD-§18-Frage unmarkiert „weggeraten" — Eskalationen festgehalten.

---

## 8. Getroffene Annahmen

| # | Annahme | Auswirkung wenn falsch | Klären mit |
| --- | --- | --- | --- |
| **A-1** | Der Standardkontenplan (`cost_category`) ist in Phase 1 für alle Organizations identisch (globale Systemstammdaten), SDD §7 („fester Standardkontenplan"). | Falls Orgs ihn anpassen/versionieren dürfen, muss er pro-Org kopiert + tenant-behaftet werden (RLS). Modell-Umbau in M2, nicht M0-kritisch. | Produkt/Fachseite, vor M2 |
| **A-2** | Ein `User` gehört zu **genau einer** Organization (kein n:m). | Verbund-/Dienstleister-Accounts über mehrere Verwaltungen bräuchten `membership` als echte n:m-Relation + erweiterten Tenant-Context. `membership` ist bereits so modelliert, dass die Erweiterung möglich bleibt. | Produkt/Fachseite |
| **A-3** | Server-seitige Sessions (Cookie) als Auth-Default (statt JWT); eigene Auth statt externem IdP. | JWT/IdP erfordern anderes Session-/Tenant-Context-Handling; Schnittstelle ist gekapselt, Wechsel möglich. | Tech-Lead/Mensch |
| **A-4** | Vermögenstrennung wird in M0 nur im Modell vorbereitet (Mandat-/Objekt-Bezug), nicht als zweite DB-RLS-Achse erzwungen. | Falls frühe DB-Härtung der Vermögenstrennung gewünscht ist, zweite Policy-Achse/Constraints nötig (frühestens sinnvoll mit M6). | Mensch, spätestens vor M6 |
| **A-5** | EUR als einzige Währung in Phase 1 → `NUMERIC(14,2)` ohne Währungsfeld ausreichend. | Mehrwährung würde Währungscode + ggf. höhere Skala erfordern. Für deutsche Hausverwaltung unwahrscheinlich. | Produkt (niedriges Risiko) |
| **A-6** | Datenhaltung in der EU (SDD §15); Betrieb als SaaS mit AV-Vertrag oder self-hosted. Beeinflusst Infra-Region, nicht das M0-Datenmodell. | Region/Hosting-Entscheidung; kein Modell-Einfluss. | Betrieb/Recht |

---

## 9. Offene Punkte (blockierend / nicht blockierend für M0/M1)

**Aus SDD §18 — relevant für M0/M1:**

| Punkt (SDD §18) | Blockiert M0/M1? | Umgang in diesem Design |
| --- | --- | --- |
| Detailtiefe Lösch-/Anonymisierungskonzept vs. revisionssichere Aufbewahrung | **Nein** für M0/M1 (Felder vorgesehen); **ja** für M9 | Struktur in Abschnitt 3.3 angelegt (`processing_restricted`, `retention_until`, `anonymized_at`); fachliche Klärung (StB/Datenschutz) vor M9. **[ENTSCHEIDUNG]** |
| Detailtiefe Mietkonto / Kaution / Kautionskonto | **Nein** (M6) | `bank_account`/`lease_agreement`-Felder vorbereitet; Tiefe in M6. |
| Gewerbeeinheiten: Vorwegabzug, USt-Option | **Teilweise M1** (Feld `usage_type=Gewerbe` existiert; Logik erst M3) | `unit.usage_type` vorgesehen; Umlagelogik/Vorwegabzug ist M3-Frage. |
| Rundungsregeln pro Abrechnung | **Nein** (M3) | Quoten exakt als `NUMERIC` gespeichert; Rundung als bewusste M3-Operation (nie Float). |
| Steuerliche Ausweise (§ 35a EStG) in Eigentümerabrechnung | **Nein** (M4) | Nur Hinweis; M4. |
| E-Rechnungs-Eingangskanäle / Validierungsstrenge | **Nein** (M5) | `invoice_document` modelliert; M5. |

**Architektur-spezifisch (von diesem Dokument aufgeworfen), die der Mensch entscheiden sollte:**

1. **[ENTSCHEIDUNG] Isolationsstrategie:** Shared DB + RLS (Option B, empfohlen) bestätigen — die folgenreichste M0-Entscheidung. ADR-0001.
2. **[ENTSCHEIDUNG] Backend-Stack:** Python/FastAPI (empfohlen) vs. Node/NestJS; bei FastAPI: SQLAlchemy+Alembic. ADR-0002.
3. **[ENTSCHEIDUNG] Auth-Modell:** Server-Session (empfohlen) vs. JWT; eigene Auth vs. IdP (A-3).
4. **[ENTSCHEIDUNG] Vermögenstrennung-Härtung:** App-Layer (M0) jetzt; DB-Härtung mit M6 entscheiden (A-4).
5. **[ENTSCHEIDUNG] Kontenplan-Geltung:** global vs. pro-Org (A-1) — vor M2.

> **Keine Selbst-Zertifizierung (Haltung):** Dieses Design macht Mandantentrennung, Unveränderbarkeit und Aufbewahrung *transparent und auditierbar* und legt die getroffenen Annahmen offen. Es kann **nicht** garantieren, dass eine spätere Abrechnung oder die Belegablage im konkreten Fall gerichts-/prüfungsfest ist. Vor Produktivsetzung der GoBD-/DSGVO-relevanten Teile (spätestens M9) ist eine fachliche Abnahme durch Steuerberater/Fachanwalt vorgesehen.

---

## 10. Was dieses Dokument bewusst NICHT entscheidet

- Konkrete Abrechnungs-/Verteilungslogik (M3/M4) — eigener `engine-correctness`-Schritt.
- Vertieftes GoBD-Belegspeicher-Design (M2) — hier nur die tragende Struktur.
- PDF-Layouts (SDD §18, offen).
- Endgültige Wahl des Unveränderbarkeits-Erzwingungsmechanismus (Trigger vs. Grants vs. frozen-Tabelle) — Detail in M2.
- Kalender-/Zeitplan (Roadmap: braucht Teamgröße/Velocity).
