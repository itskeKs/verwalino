# M4-Design: WEG-Abrechnung — Wirtschaftsplan, Jahresabrechnung mit Abrechnungsspitze, Vermögensbericht

Stand: 2026-06-02 · Version 0.1 (Design, vor Implementierung)
Bezug: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 §6.3, §6.9, §7.3, §8, §10, §11, §13, §14, §16, §17, §18 · [roadmap.md](roadmap.md) M4 · [architecture.md](architecture.md) §3 (Unveränderbarkeit), §5.1 (Konventionen), §5.3/§5.4 (Entitäten) · [m3-abrechnungs-engine.md](m3-abrechnungs-engine.md) (Engine, `run_type`, Snapshot, Golden-Tests)
ADRs zu diesem Dokument: [adr/0003-…](adr/0003-gobd-unveraenderbarkeit-mechanik.md) (wiederverwendet) · [adr/0004-zeitanteilige-verteilung-tagegenau.md](adr/0004-zeitanteilige-verteilung-tagegenau.md) (wiederverwendet, M4 löst den dort offen gelassenen Plan-Maßstab) · [adr/0005-rundung-largest-remainder.md](adr/0005-rundung-largest-remainder.md) (wiederverwendet) · **[adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md](adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md) (neu)** · **[adr/0008-vermoegensbericht-mvp-umfang.md](adr/0008-vermoegensbericht-mvp-umfang.md) (neu)**
Autor: `engine-correctness` (Opus 4.8) · Status: **Entwurf zur Bestätigung durch den Menschen — kein Anwendungscode, keine Migration, kein Commit**

> Dieses Dokument spezifiziert die **WEG-Outputs** (M4): **Wirtschaftsplan** (vorwärtsgerichtete Hausgeldvorschüsse), **Hausgeld-/Jahresabrechnung mit Abrechnungsspitze (WEMoG, § 28 II WEG)** und **Vermögensbericht (§ 28 IV WEG)**. M4 ist die **Eigentümer-Seite** des Abrechnungskerns; M3 (Betriebskostenabrechnung für Mieter, 227 Tests, live verifiziert inkl. PDF) ist die Mieter-Seite. **M4 erfindet die Verteilungs-Engine nicht neu — es erweitert sie um einen Eigentümer-Modus.**
>
> **Verbindlichkeit:** SDD (v0.2), Roadmap, `architecture.md`, `m3-abrechnungs-engine.md` und die ADRs 0001–0006 sind die Quelle der Wahrheit und bleiben **unangetastet**. Dieses Dokument ist additiv (neues Doc + ADR-0007/0008). Entscheidungen, die der Mensch bestätigen sollte, sind **[ENTSCHEIDUNG]**; offene SDD-§18-/fachliche Punkte **[§18/fachlich]** (nicht eigenmächtig final entschieden; provisorische, klar markierte Annahme; fachliche Abnahme empfohlen).
>
> **Keine Selbst-Zertifizierung:** Dieses Design macht Wirtschaftsplan, Jahresabrechnung, Abrechnungsspitze, Rücklagen-Entwicklung und Vermögensbericht *transparent, nachrechenbar und auditierbar* und legt jede Annahme offen. Es kann **nicht** garantieren, dass eine konkrete WEG-Abrechnung gerichts-/prüfungs-/beschlussfest ist. Die fachliche Richtigkeit (insb. Abrechnungsspitze nach WEMoG, Trennung umlagefähig/nicht/Rücklage, § 28 IV-Umfang, § 35a EStG-Ausweis) ist vor produktivem Einsatz durch **Steuerberater/Fachanwalt für WEG-Recht** abzunehmen.

---

## 0. Geltungsbereich, Exit-Kriterium, Anschluss an M0–M3

**M4-Exit (Roadmap):** „Wirtschaftsplan, Jahresabrechnung und Vermögensbericht für eine WEG sind als PDF erzeugbar; **Beträge sind konsistent**." Das ist die objektive Messlatte. „Konsistent" wird durch die **synthetischen Golden-Tests** (§9) mit **handberechneter Abrechnungsspitze** und durch die **Property-Invarianten** belegt (Σ Eigentümeranteile == Gesamtkosten; MEA-Summen schlüssig; Rücklagen-Entwicklung Anfang + Zuführung − Entnahme == Ende).

**M4 baut auf einem verifizierten Fundament auf (M0–M3, 227 Tests grün) und übernimmt dessen Konventionen unverändert.** Die wichtigste Erkenntnis aus der Code-Durchsicht: **das M3-Fundament wurde bereits M4-fähig gebaut.** M4 ist additiv, nicht umbauend.

| Vorhandenes Fundament (M0–M3, live) | M4 nutzt es so |
| --- | --- |
| `settlement_run.run_type` **CHECK** enthält bereits `WEG_JAHRESABRECHNUNG`, `WIRTSCHAFTSPLAN`, `VERMOEGENSBERICHT` (Migration `0009`, reserviert) | M4 implementiert diese Werte; **kein Schemabruch, keine neue `run_type`-Migration**. |
| `settlement_run`-Spalten `property_id`, `management_mandate_id`, `period_*`, `unit_id` (NULL für objektweite Läufe), `snapshot_payload`/`_sha256`, `status`, `superseded_by_id`, `engine_version` | M4-WEG-Läufe sind **objektweit** (`unit_id`/`lease_agreement_id`/`tenant_profile_id` = NULL); Snapshot/Hash/Korrektur-Kette unverändert. |
| `settlement_line` mit `line_type` **CHECK** (`COST`, `ADVANCE_PAYMENT`, `BALANCE`, `SUBTOTAL`), `distribution_key`, `total_amount`, `distribution_base_total/_share`, `amount`, `note` | M4 nutzt dieselbe Tabelle; **`line_type`-Vokabular wird additiv erweitert** (§3.2, neue Migration nur für CHECK + `addressee_*`-Spalten). |
| `cost_entry.owner_distribution_key` (vorhanden, in M3 ungenutzt) | **M4-Primäreingabe** für die Eigentümer-Verteilung je Kostenposition. |
| `cost_category`: `owner_billing_relevant_default` (alle `True`), `owner_distribution_key_default` (meist `MEA`), `reserve_relevant` (**`True` nur für `ERHALTUNGSRUECKLAGE`**), `cost_group` (`UMLAGEFAEHIG`/`GEMISCHT_PRUEFEN`/`NICHT_UMLAGEFAEHIG`) | **Trennung umlagefähig/nicht/Rücklage** (§3, §6.3) folgt direkt aus diesen bereits geseedeten Feldern. |
| `ownership_history` (temporal, EXCLUDE pro `(unit, owner)`, `mea_share`, Co-Eigentum + Lücken erlaubt) | Eigentümer-Adressat je Tag (tagegenau, ADR-0004); **Miteigentum** über `mea_share` (§4). |
| Engine: `distribute_position` verteilt **bereits** jede Position über **alle (unit, time-slice)-Zellen** des ganzen Objekts und attribuiert **Leerstandszellen an Eigentümer** (`vacancy_attribution`, `OwnerPeriodInput` werden geladen) | M4 ist der **gespiegelte Adressatenfokus**: nicht „eine Mieterzelle herausgreifen", sondern **alle Eigentümerzellen je Eigentümer summieren** (§4.3). Die Zell-Mathematik (Zeit × Schlüssel, Largest-Remainder) ist **identisch**. |
| `compute_bk_settlement` als reine Funktion; `SettlementService` mit `create_draft`/`compute`/`preview`/`finalize`/`create_correction_run` (atomar, Audit); `build_snapshot_payload` + `canonical_json` + `sha256_hex`; `render_settlement_pdf` (WeasyPrint, rechnet nichts) | M4 fügt **`compute_weg_jahresabrechnung`**, **`compute_wirtschaftsplan`**, **`build_vermoegensbericht`** als reine Funktionen hinzu und erweitert den Service/PDF analog (§10). |
| ADR-0003-Trigger (`status='finalized'` → unveränderbar), RLS (vier Policies, fail-closed), `TENANT_TABLES`-Coverage-Test | **Wiederverwendet** — `settlement_run`/`settlement_line` haben das Muster bereits; M4 fügt **keine neuen Tabellen mit Trigger/RLS** hinzu außer `economic_plan`/`economic_plan_line` (§1, gleiches Muster). |
| `app/core/temporal.py` (`db_exclusive_to_engine_inclusive`, ADR-0006), tagegenaue `periods.py`, `Decimal`/`NUMERIC(14,2)`/`NUMERIC(12,6)` | Durchgängig übernommen, **gleiche Lese-Grenze** für `ownership_history`. |

**Nicht in M4:** Echte Zahlungen/Mietkonto (M6 — die **Ist-Vorschüsse** des Eigentümers werden in M4 **manuell** erfasst, analog M3 `advance_payments_source='MANUAL'`, §2.4). Bankkontensalden mit Buchungslogik (M6 — der Vermögensbericht nutzt in M4 einen **manuell erfassten Stichtagssaldo**, §6). Versammlung/Beschluss-Sammlung (M8 — M4 erzeugt **Unterlagen** für die Beschlussfassung, fasst aber keine Beschlüsse, SDD §10). § 35a EStG-Ausweis (SDD §18, [§18/fachlich], §8.4).

**Architektur-Leitsatz (SDD §16, hier hart, wie in M3):** Die **Berechnungs-Engine bleibt eine Schicht reiner Funktionen** (`Decimal` ein/aus, keine DB/HTTP/I/O), getrennt von Persistenz (Snapshot), API und PDF-Layout. M4-Logik kommt in dasselbe `app/engine/`-Paket, nicht in Services.

---

## 1. Datenmodell

### 1.1 [ENTSCHEIDUNG] Jahresabrechnung + Vermögensbericht als `settlement_run`-`run_type` vs. eigene Entität — Empfehlung: **gleiche `settlement_run`-Hülle**

M3 §1.3 (E3-6) hat das bereits empfohlen und im Schema **reserviert**. M4 löst es ein. Begründung im Detail:

**Empfehlung: Jahresabrechnung (`WEG_JAHRESABRECHNUNG`) und Vermögensbericht (`VERMOEGENSBERICHT`) sind `settlement_run`-`run_type`s, keine eigenen Entitäten.**

- **Gleiche Unveränderbarkeits-/Snapshot-/Audit-/Korrektur-Mechanik.** Beide sind **finalisierte, rechtsverbindliche, einzufrierende Dokumente** mit exakt derselben Anforderung wie die BK-Abrechnung: unveränderbarer Snapshot (`snapshot_payload` + `snapshot_sha256`), Korrektur = neuer Lauf (`superseded_by_id`), GoBD-Trigger (`status='finalized'`), RLS, Audit-Trail. Das ist **fertig** (Migrationen `0009`–`0011`). Eine eigene Entität müsste die ganze Mechanik duplizieren — genau das Anti-Pattern, das ADR-0003 §3 vermeiden wollte.
- **`run_type`-Vokabular ist schon da** (CHECK in `0009`). Es entsteht **keine** Schemabruch-Migration.
- **`settlement_line` trägt beide.** Die Jahresabrechnung ist strukturell „Gesamtkosten je Kostenart + MEA-Verteilung + Soll/Ist je Eigentümer" — dasselbe Zeilen-Modell wie BK, plus wenige neue `line_type`-Werte (§3.2). Der Vermögensbericht ist eine **Bestandsaufstellung** — er passt als kleine Menge `line_type`-Zeilen (Rücklagenstand, Bankkonto-Stand, …) plus strukturierter `snapshot_payload`; falls `settlement_line` für den Bericht zu eng wäre, lebt die Detailstruktur ausschließlich im typisierten Payload (die Hülle bleibt dieselbe).

**Bewusst in Kauf genommen:** `settlement_run` trägt damit drei sehr verschiedene fachliche Dokumenttypen (Soll-Ist-Abrechnung, Plan, Bestand). Das ist akzeptabel, weil (a) `run_type` der saubere Diskriminator ist, (b) typabhängige Validierung im Service liegt (nicht im Schema), (c) der gemeinsame Nenner — *einfrierbares, gehashtes, mandantengetrenntes, korrigierbares Dokument* — exakt das ist, was alle drei brauchen. Die Alternative (drei Entitäten) verdreifacht Trigger, RLS-Policies, Coverage-Tests und Korrektur-Logik ohne fachlichen Gewinn.

> **[ENTSCHEIDUNG E4-1]** Jahresabrechnung + Vermögensbericht als `run_type` derselben `settlement_run`-Hülle (empfohlen, M3-E3-6-Linie fortgeführt) vs. eigene Entitäten. Empfehlung: **gleiche Hülle**. Bestätigen.

### 1.2 [ENTSCHEIDUNG] Wirtschaftsplan als eigene Entität `economic_plan` / `economic_plan_line` — Empfehlung: **eigene Entität (SDD §11/§13 sieht `EconomicPlan`/`EconomicPlanLine` vor)**

Der Wirtschaftsplan ist fachlich **etwas anderes** als eine Abrechnung und sollte **nicht** in `settlement_run` gezwängt werden:

- **Vorwärtsgerichtet, kein Soll-Ist.** Er hat **keine** Ist-Kosten, **keine** Vorauszahlungssumme, **keinen** Saldo, **keine** `service_period`-Kostenpositionen. Er hat geplante Kosten je Kategorie und daraus berechnete **monatliche Hausgeldvorschüsse je Eigentümer**. Würde man ihn in `settlement_run` legen, blieben dessen Kernspalten (`advance_payments_total`, `balance`, `lease_agreement_id`, …) dauerhaft NULL/bedeutungslos — ein Zeichen für die falsche Hülle.
- **SDD §11/§13 nennt `EconomicPlan`/`EconomicPlanLine` explizit als eigene Entitäten.** Das Datenmodell-Kapitel listet sie getrennt von `SettlementRun`/`SettlementLine`. M4 folgt der SDD.
- **Andere Lebensdauer/Beziehung zur Abrechnung.** Der Plan eines Jahres ist die **Soll-Vorschuss-Quelle** für die Abrechnungsspitze **desselben** Jahres (§2). Das ist eine saubere FK-Beziehung `settlement_run.economic_plan_id → economic_plan.id`, keine Vermischung in einer Tabelle.

**Aber: gleiche Querschnitts-Mechanik wiederverwenden.** `economic_plan`/`economic_plan_line` bekommen **dasselbe** Muster wie `settlement_run`/`settlement_line`:
- `organization_id` + RLS (vier Policies, fail-closed, Muster `0010`),
- `status` (`draft`|`finalized`), `snapshot_payload` JSONB + `snapshot_sha256`, `engine_version`, `finalized_at/_by`, `superseded_by_id` (Vorwärts-Zeiger, E-4),
- ADR-0003-Trigger (`status='finalized'` → unveränderbar; einzig erlaubte Mutation `superseded_by_id`),
- `TENANT_TABLES`-Coverage-Test erweitert.

So bekommt der Plan **dieselbe Unveränderbarkeits-Garantie** (ein beschlossener Wirtschaftsplan ist ein Dokument, das eingefroren bleiben muss), ohne in die Abrechnungs-Hülle gezwängt zu werden.

> **[ENTSCHEIDUNG E4-2]** Wirtschaftsplan als **eigene Entität** `economic_plan`/`economic_plan_line` (empfohlen, SDD §11/§13) vs. weiterer `settlement_run.run_type`. Empfehlung: **eigene Entität**, gleiche Snapshot-/Trigger-/RLS-Mechanik. Bestätigen. *(Der reservierte `run_type='WIRTSCHAFTSPLAN'` bleibt im CHECK ungenutzt — kein Schaden; alternativ in einer additiven Migration entfernen, nicht nötig.)*

### 1.3 Schema `economic_plan` (neue Tabelle, tenant-behaftet, RLS, ab `finalized` unveränderbar)

| Spalte | Typ | Bedeutung / Begründung |
| --- | --- | --- |
| `id` | `UUID` PK | `gen_random_uuid()`. |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | die WEG. |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | WEG-Mandat (Vermögenstrennung-Anker). |
| `plan_year` | `INTEGER NOT NULL` | Wirtschaftsjahr (i. d. R. Kalenderjahr; SDD §12). |
| `period_start`/`period_end` | `DATE NOT NULL` | Plan-Zeitraum (meist 01.01.–31.12.). `CHECK (period_end >= period_start)`. |
| `monthly_due_day` | `SMALLINT NULL` | Fälligkeitstag des monatlichen Hausgelds (Anzeige/spätere Sollstellung M6); reine Information in M4. |
| `reserve_contribution_total` | `NUMERIC(14,2) NOT NULL DEFAULT 0` | geplante **Gesamt-Rücklagenzuführung** des Jahres (keine Kostenumlage, §3). |
| `status` | `String(12) NOT NULL DEFAULT 'draft'` | `draft`\|`finalized`. CHECK. Einfrier-Bedingung `finalized`. |
| `snapshot_payload` | `JSONB NULL` | kanonische, vollständige Plan-Basis + Ergebnis (§5.4). |
| `snapshot_sha256` | `String(64) NULL` | SHA-256 über kanonische Serialisierung (wie M3). |
| `engine_version` | `String(20) NOT NULL` | z. B. `m4.1.0`. |
| `finalized_at`/`finalized_by` | `TIMESTAMPTZ` / `UUID NULL` FK→`user` (`SET NULL`) | Audit. |
| `superseded_by_id` | `UUID NULL` FK self (`RESTRICT`) | Korrektur-Kette (E-4). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | wie überall. |

Constraints: `CHECK (period_end >= period_start)`, `CHECK (status IN ('draft','finalized'))`. RLS vier Policies. Indizes: `organization_id`; `property_id`; `(property_id, plan_year)`; `superseded_by_id`. Grant: volles CRUD (editierbarer `draft`, Trigger-only-Freeze — E3-5-Linie aus M3).

### 1.4 Schema `economic_plan_line` (gehört zum Snapshot des Plans)

Eine Zeile ist **eine geplante Position** — entweder eine Kostenart-Planung oder die Rücklagenzuführung oder die je-Eigentümer-Aufteilung.

| Spalte | Typ | Bedeutung |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `economic_plan_id` | `UUID NOT NULL` FK→`economic_plan` (`RESTRICT`) | Zugehörigkeit. |
| `line_type` | `String(24) NOT NULL` | `PLANNED_COST` (geplante Kostenart) \| `RESERVE_CONTRIBUTION` (Rücklagenzuführung, **keine** Kostenumlage) \| `OWNER_ADVANCE` (je-Eigentümer-Jahres-/Monatsvorschuss) \| `SUBTOTAL`. CHECK. |
| `cost_category_id`/`cost_category_code` | `UUID NULL` / `String(50) NULL` | bei `PLANNED_COST`: die geplante Kostenart (Code denormalisiert, prüffest). |
| `owner_profile_id` | `UUID NULL` FK→`owner_profile` (`RESTRICT`) | bei `OWNER_ADVANCE`: der Eigentümer. |
| `unit_id` | `UUID NULL` FK→`unit` (`RESTRICT`) | bei `OWNER_ADVANCE`: die Einheit (ein Eigentümer kann mehrere Einheiten haben → mehrere Zeilen). |
| `label` | `String(255) NOT NULL` | Anzeigename. |
| `distribution_key` | `String(20) NULL` | bei `PLANNED_COST`/`OWNER_ADVANCE`: angewandter Schlüssel (i. d. R. `MEA`). CHECK gegen Schlüsselvokabular. |
| `distribution_base_total`/`_share` | `NUMERIC(12,6) NULL` | Nenner/Zähler (z. B. Gesamt-MEA / MEA der Einheit) — Rechenweg. |
| `planned_amount` | `NUMERIC(14,2) NOT NULL` | geplanter Jahresbetrag der Zeile (Kostenart-Plan, Rücklagenzuführung, Eigentümer-Jahresanteil). |
| `monthly_amount` | `NUMERIC(14,2) NULL` | bei `OWNER_ADVANCE`: monatlicher Hausgeldbetrag (§5.3, Maßstab-Entscheidung E4-3). |
| `cost_group` | `String(20) NULL` | `UMLAGEFAEHIG`\|`GEMISCHT_PRUEFEN`\|`NICHT_UMLAGEFAEHIG` (denormalisiert aus `cost_category`, für die Plan-Trennung). |
| `sort_order` | `SMALLINT NOT NULL DEFAULT 0` | feste PDF-Reihenfolge. |
| `note` | `String(500) NULL` | Hinweis. |
| `created_at` | `TIMESTAMPTZ` | |

Constraints: `CHECK (line_type IN (...))`; `CHECK (distribution_key IS NULL OR distribution_key IN (...))`. RLS vier Policies. Indizes: `organization_id`; `economic_plan_id`; `(economic_plan_id, sort_order)`. Grant: volles CRUD (Trigger-only-Freeze über Eltern-Plan-Status, exakt wie `settlement_line` in `0011`).

### 1.5 Verknüpfung Jahresabrechnung ↔ Wirtschaftsplan (für die Abrechnungsspitze)

Die Abrechnungsspitze ist `Ist − Soll`, und das **Soll** sind die **beschlossenen Wirtschaftsplan-Vorschüsse** desselben Jahres (§2). Damit der finalisierte Abrechnungs-Snapshot **prüffest** auf seine Soll-Quelle verweist:

- Neue Spalte **`settlement_run.economic_plan_id`** `UUID NULL` FK→`economic_plan` (`RESTRICT`), gesetzt bei `WEG_JAHRESABRECHNUNG` (NULL für `BK_MIETER`). Additive Migration (§10.2). Begründung `RESTRICT`: ein Plan, auf den eine finalisierte Abrechnung verweist, darf nicht gelöscht werden (GoBD-Kette).
- Zusätzlich werden die **Soll-Vorschüsse je Eigentümer als Kopie** in den Abrechnungs-`snapshot_payload` eingefroren (nicht nur die Plan-ID), damit die Abrechnung **ohne** Rückgriff auf den Plan reproduzierbar bleibt (Snapshot-Philosophie, M3 §1.2). Der Plan-Bezug (`economic_plan_id`) ist die Audit-/Anzeige-Verknüpfung; die eingefrorene Kopie ist die Rechen-/Reproduktionsgrundlage.

> **[§18/fachlich F4-1] Soll-Quelle, wenn kein Plan beschlossen wurde.** Es kann WEGs ohne förmlich beschlossenen Wirtschaftsplan für das Abrechnungsjahr geben (z. B. neue WEG, verspäteter Beschluss). Was ist dann das „Soll"? Optionen: (a) Soll = die tatsächlich **geleisteten** Vorschüsse (dann ist die „Spitze" = Ist-Kosten − geleistete Zahlungen, was sich der reinen Soll-Ist-Abrechnung annähert); (b) Pflichtfeld — keine Jahresabrechnung ohne hinterlegten Plan. **Nicht eigenmächtig entschieden.** Provisorische Annahme für M4-Testfälle: `economic_plan_id` ist **erforderlich** für `WEG_JAHRESABRECHNUNG`; die Soll-Vorschüsse je Eigentümer kommen aus dem (mindestens als `draft` erfassten) Plan. Fachliche Klärung (StB/Fachanwalt) vor produktiv. Siehe ADR-0007.

### 1.6 Was M4 am bestehenden Schema **nicht** anfasst

`settlement_run`/`settlement_line` bleiben strukturell wie in M3; M4 fügt **additiv** hinzu: (a) `settlement_run.economic_plan_id` (§1.5), (b) erweitertes `line_type`-CHECK auf `settlement_line` (§3.2), (c) optionale `settlement_line.addressee_owner_profile_id` für die Eigentümer-Zuordnung (§4.4). Alles über **neue** Migrationen (`0012`+), nie durch Ändern von `0009`–`0011` (additive Disziplin, wie M3 sie etabliert hat).

---

## 2. Hausgeld-/Jahresabrechnung mit Abrechnungsspitze (WEMoG) — Kern-Korrektheit

Dies ist die korrektheitskritischste Stelle von M4, analog zur tagegenauen Mieterwechsel-Logik in M3. Sie ist **rechtlich spezifisch** und wird leicht falsch gemacht (deshalb der eigene ADR-0007).

### 2.1 Was die Abrechnungsspitze rechtlich ist (post-WEMoG, § 28 II WEG)

Vor der WEG-Reform 2020 wurde die **gesamte Jahresabrechnung** beschlossen. **Seit dem WEMoG (§ 28 Abs. 2 WEG, gilt seit 01.12.2020)** ist **Beschlussgegenstand nur noch die Anpassung der Vorschüsse — die „Abrechnungsspitze":**

```
Abrechnungsspitze (je Eigentümer)
   = anteilige IST-Kosten des Eigentümers   (lt. Verteilerschlüssel/MEA, tagegenau)
   − beschlossene SOLL-Vorschüsse des Eigentümers  (lt. Wirtschaftsplan desselben Jahres)
```

- **Abrechnungsspitze > 0 → Nachschuss** (der Eigentümer schuldet mehr, als der Plan vorsah).
- **Abrechnungsspitze < 0 → Anpassungsguthaben / Minderung** (der Eigentümer hat per Plan-Soll mehr eingeplant, als die Ist-Kosten ergaben).

**Wichtige fachliche Abgrenzung (der häufigste Fehler):** Die Abrechnungsspitze ist **`Ist − Soll-Vorschuss`**, **nicht** `Ist − tatsächlich-gezahlte-Vorschüsse`. Was der Eigentümer **tatsächlich** gezahlt (oder nicht gezahlt) hat, ist eine **separate** Größe (Zahlungsrückstand/Guthaben auf dem Hausgeldkonto), die in M4 **nachrichtlich** ausgewiesen, aber **nicht** mit der beschlussrelevanten Abrechnungsspitze vermischt wird. Beschlussgegenstand ist die Spitze gegen das **Soll**; der Zahlungsstand ist Vollstreckungs-/Mahnsache (M6/M7).

> **[§18/fachlich F4-2]** Diese `Ist − Soll`-Definition der beschlussrelevanten Spitze ist die nach h. M. zum WEMoG übliche; die genaue Darstellung (was als „beschlossen", was als „nachrichtlich") und der Umgang mit nicht beschlossenem Plan (F4-1) sind fachlich (Fachanwalt WEG) abzunehmen. ADR-0007 hält die Definition und die Annahme fest; **keine Selbst-Zertifizierung.**

### 2.2 Was die Jahresabrechnung je Eigentümer ausweist

Die Abrechnung weist pro Eigentümer **strukturiert** (in `settlement_line`, nicht nur im PDF) aus — so ist „konsistent" (M4-Exit) maschinell prüfbar:

| Ausweis | Träger im Modell | Bemerkung |
| --- | --- | --- |
| **Gesamtkosten je Kostenart** (objektweit) | `settlement_line(line_type='COST').total_amount` | Gesamtsumme der WEG je Kategorie. |
| **angewandter Schlüssel je Position** | `settlement_line.distribution_key` (+ `_base_total`/`_share`) | i. d. R. `MEA`; kategorie-spezifisch möglich (§4). |
| **MEA-Anteil des Eigentümers je Kostenart** | `settlement_line.amount` (gerundet, ADR-0005) | der dem Eigentümer zugerechnete Betrag. |
| **Summe anteilige Ist-Kosten des Eigentümers** | `settlement_line(line_type='OWNER_COST_TOTAL')` (neu, §3.2) + `settlement_run.owner_cost_total` (im Payload) | nur **umlagefähige + nicht-umlagefähige** WEG-Kosten; **ohne** Rücklagenzuführung (die ist keine Kosten, §3). |
| **Soll-Vorschüsse des Eigentümers** (lt. Plan) | `settlement_line(line_type='PLAN_ADVANCE')` (neu) | aus `economic_plan` (§1.5), in den Snapshot kopiert. |
| **Abrechnungsspitze (Nachschuss/Guthaben)** | `settlement_line(line_type='ABRECHNUNGSSPITZE')` (neu) + Payload `abrechnungsspitze` | = anteilige Ist-Kosten − Soll-Vorschüsse (§2.1). **Der beschlussrelevante Betrag.** |
| **Rücklagenzuführung des Eigentümers** | `settlement_line(line_type='RESERVE_CONTRIBUTION')` (neu) | getrennt ausgewiesen, **keine Kostenposition** (§3). |
| **Rücklagen-Entwicklung** (objektweit) | eigene `RESERVE_*`-Zeilen (§3.3) | Anfang/Zuführung/Entnahme/Ende. |
| **(nachrichtlich) tatsächlich geleistete Vorschüsse / Zahlungsstand** | `settlement_line(line_type='ADVANCE_PAYMENT')` optional + Payload | **getrennt** von der Spitze (§2.1); in M4 **manuell** (§2.4), M6 `DERIVED`. |

### 2.3 Trennung umlagefähig / nicht umlagefähig / Rücklage (SDD §6.3, §7.3, §11)

Anders als die BK-Mieterabrechnung (die nur umlagefähige Kosten zeigt) weist die **Eigentümerabrechnung alle drei Gruppen** aus, aber **getrennt** — `cost_category.cost_group` liefert die Gruppe direkt (bereits geseedet):

- **Umlagefähig (`cost_group='UMLAGEFAEHIG'`)** und **gemischt (`GEMISCHT_PRUEFEN`)**: laufende Bewirtschaftungskosten. Für die Eigentümerabrechnung wird i. d. R. der **`gross_amount`** verteilt (der Eigentümer trägt die Gesamtkosten anteilig; die Frage „welcher Teil ist auf Mieter umlegbar" ist die **Mieter**-Sphäre M3, nicht die Eigentümer-Sphäre). [§18/fachlich F4-3, §4.5]
- **Nicht umlagefähig (`NICHT_UMLAGEFAEHIG`)**: Verwaltung, Instandhaltung/-setzung, Rechtsberatung, Bankgebühren etc. Sie erscheinen in der **Eigentümer**abrechnung (anteilig), nie in der Mieter-BK. `owner_billing_relevant_default=True` ist für alle geseedet.
- **Rücklage (`reserve_relevant=True`, nur `ERHALTUNGSRUECKLAGE`)**: die Zuführung ist **keine Kostenumlage**, sondern eine **Vermögensbildung** (§3). Sie wird **getrennt** ausgewiesen und **nicht** in die „anteilige Ist-Kosten"-Summe der Abrechnungsspitze einbezogen (§3.2).

Die Engine summiert je Eigentümer **drei Subtotals** (`SUBTOTAL`-Zeilen): Σ umlagefähig, Σ nicht umlagefähig, Σ Rücklagenzuführung — exakt wie SDD §11 für Eigentümerabrechnungen verlangt.

### 2.4 Herkunft der Soll-Vorschüsse und (nachrichtlich) der Ist-Zahlungen — M4 Plan-Soll + manuell

- **Soll-Vorschüsse (beschlussrelevant):** aus dem verknüpften `economic_plan` (§1.5), je Eigentümer/Einheit, in den Snapshot kopiert. Das ist die **maßgebliche** Größe für die Abrechnungsspitze.
- **Ist-Zahlungen (nachrichtlich):** Zahlungen existieren noch nicht (M6). Wie in M3 wird ein **manuell erfasster** Betrag je Eigentümer geführt (`advance_payments_source='MANUAL'`), der **nur nachrichtlich** (Zahlungsstand) ausgewiesen wird — **nicht** in die beschlussrelevante Spitze einfließt (§2.1). Ab M6 `DERIVED` aus echten `Payment`s.

### 2.5 Lebenszyklus, Snapshot, Korrektur — identisch zu M3

`draft → finalize → finalized` (unveränderbar, ADR-0003). Korrektur = neuer `WEG_JAHRESABRECHNUNG`-Lauf (`superseded_by_id`). `finalize` ist **eine atomare Transaktion** (Engine final rechnen → Soll-Vorschüsse einfrieren → `snapshot_payload` → `snapshot_sha256` → `settlement_line` (re)schreiben → `status='finalized'`/`finalized_at/by` → `audit_event`). Das ist **dieselbe** `SettlementService.finalize`-Struktur wie M3, nur mit dem WEG-Compute. Determinismus/Hash-Stabilität wie M3 (ADR-0005).

---

## 3. Erhaltungsrücklage — Zuführung ist keine Kostenumlage; Entwicklung ausweisen

### 3.1 Grundsatz: Vermögensbildung, nicht Verbrauch

Die **Zuführung** zur Erhaltungsrücklage (früher „Instandhaltungsrücklage") ist **keine Bewirtschaftungs-/Kostenposition**, sondern eine **Ansparung von Gemeinschaftsvermögen**. Sie ist **owner-only** (Mieter tragen sie nie — `tenant_apportionability_default='NICHT_UMLAGEFAEHIG'` ist für `ERHALTUNGSRUECKLAGE` geseedet) und wird in der Jahresabrechnung **getrennt** ausgewiesen. Sie darf **nicht** in die „anteilige Ist-Kosten"-Summe einfließen, die in die Abrechnungsspitze eingeht — sonst würde Vermögensbildung als verbrauchte Kosten gezählt (fachlicher Fehler).

> **Modell-Konsequenz:** Eine `cost_entry` der Kategorie `ERHALTUNGSRUECKLAGE` (`reserve_relevant=True`) wird von der WEG-Engine **als Rücklagenzuführung** behandelt (eigene `RESERVE_CONTRIBUTION`-Zeile), nicht als `COST`. Die Verteilung auf die Eigentümer erfolgt trotzdem nach Schlüssel (i. d. R. MEA), damit jeder Eigentümer seinen Zuführungsanteil sieht — aber sie zählt in eine **andere** Summe.

### 3.2 Neue `line_type`-Werte (additive CHECK-Erweiterung auf `settlement_line`)

Migration `0012` erweitert das `line_type`-CHECK additiv (alte Werte bleiben gültig):

| neuer `line_type` | Bedeutung |
| --- | --- |
| `OWNER_COST_TOTAL` | Summe der anteiligen Ist-Kosten des Eigentümers (umlagefähig + nicht umlagefähig, **ohne** Rücklage) — der Minuend der Spitze. |
| `PLAN_ADVANCE` | Soll-Vorschuss des Eigentümers lt. Wirtschaftsplan — der Subtrahend der Spitze. |
| `ABRECHNUNGSSPITZE` | `OWNER_COST_TOTAL − PLAN_ADVANCE` — der beschlussrelevante Nachschuss/Guthaben. |
| `RESERVE_CONTRIBUTION` | Zuführungsanteil des Eigentümers zur Erhaltungsrücklage (getrennt). |
| `RESERVE_OPENING` | Anfangsbestand der Rücklage (objektweit, Bestandszeile). |
| `RESERVE_WITHDRAWAL` | Entnahme aus der Rücklage im Jahr (objektweit). |
| `RESERVE_CLOSING` | Endbestand der Rücklage (objektweit). |

Bestehende `COST`/`ADVANCE_PAYMENT`/`BALANCE`/`SUBTOTAL` bleiben für BK (M3) und für Subtotals. **Keine Änderung** an M3-Verhalten (BK nutzt die neuen Typen nicht).

### 3.3 Rücklagen-Entwicklung (Anfangsbestand → Zuführung → Entnahme → Endbestand)

Die Jahresabrechnung (und der Vermögensbericht, §6) weisen die **Entwicklung** der Erhaltungsrücklage aus — eine harte Konsistenz-Invariante:

```
Endbestand = Anfangsbestand + Σ Zuführungen − Σ Entnahmen
```

- **Anfangsbestand** (`RESERVE_OPENING`): Rücklagenstand zu Periodenbeginn. **[§18/fachlich F4-4]** Datenquelle: manuell erfasster Stichtagswert (M4) bzw. Endbestand der Vorjahresabrechnung (sobald Vorjahres-Läufe existieren). In M4 **manuell** am Lauf erfasst, bis M6 Bankkonten-Salden real macht.
- **Σ Zuführungen**: Summe aller `ERHALTUNGSRUECKLAGE`-`cost_entry`s im Zeitraum (= Σ `RESERVE_CONTRIBUTION` über alle Eigentümer).
- **Σ Entnahmen**: Entnahmen aus der Rücklage (z. B. zur Finanzierung einer Instandsetzung). **[§18/fachlich F4-5]** In M4 **manuell** erfasst (es gibt noch keine Buchungslogik, die eine Instandsetzungs-`cost_entry` automatisch als Rücklagenentnahme markiert). Provisorisch: manuelle Entnahme-Eingabe am Lauf; fachliche Klärung der Verknüpfung Entnahme↔finanzierte Maßnahme vor produktiv.
- **Endbestand** (`RESERVE_CLOSING`): berechnet; die Engine **prüft** die Invariante und eskaliert bei Inkonsistenz (fail-loud, kein stilles Verrechnen).

Die Rücklage ist **owner-only**: Sie erscheint ausschließlich in WEG-/Eigentümer-Dokumenten, nie in der Mieter-BK.

> **[§18/fachlich F4-6] Verzinsung / mehrere Rücklagen.** Manche WEGs führen mehrere Rücklagen oder verzinste Konten. M4-MVP: **eine** Erhaltungsrücklage (`reserve_relevant`), keine Zinsbuchung. Erweiterung später; hier festgehalten, nicht weggeraten.

---

## 4. Eigentümer-Verteilung — MEA primär, kategorie-spezifische Schlüssel, tagegenau, Miteigentum

### 4.1 MEA als Primärschlüssel

WEG-Kosten werden grundsätzlich nach **Miteigentumsanteil (MEA)** verteilt (§ 16 II WEG). Die Datenquelle ist `unit.mea` (Gewicht je Einheit) bzw. bei Miteigentum `ownership_history.mea_share` (Aufteilung des Einheit-MEA auf mehrere Bruchteilseigentümer, §4.3). Das ist exakt der `MEA`-Schlüssel, den die M3-Engine bereits implementiert (`_unit_weight(unit, "MEA")` = `unit.mea`).

### 4.2 Kategorie-spezifische Schlüssel (`cost_entry.owner_distribution_key`)

Die WEG kann **per Beschluss/Teilungserklärung** für einzelne Kostenarten einen **anderen** Schlüssel als MEA festlegen (§ 16 II 2 WEG erlaubt abweichende Verteilung). Das Modell trägt das bereits: **`cost_entry.owner_distribution_key`** (per Position) und `cost_category.owner_distribution_key_default` (Default je Kategorie, meist `MEA`, bei Verbrauchskosten `VERBRAUCH`/`EXTERNE_ABRECHNUNG` — geseedet). M4 verwendet für die WEG-Abrechnung den **`owner_distribution_key`** (analog M3, das `tenant_distribution_key` nutzt). Dasselbe Schlüsselvokabular (`MEA`, `WOHNFLAECHE`, `BEHEIZTE_FLAECHE`, `EINHEIT`, `PERSONENZAHL`, `VERBRAUCH`, `EXTERNE_ABRECHNUNG`, `MANUELLER_BETRAG`) — **die Engine kann alle bereits.**

### 4.3 Eigentümerwechsel tagegenau + Miteigentum

- **Eigentümerwechsel innerhalb des Jahres** (Verkauf): tagegenau über `ownership_history` (ADR-0004). Verkäufer trägt seine Tage, Käufer seine Tage. Das ist die **gespiegelte** Mieterwechsel-Logik aus M3 §3 — dieselbe `time_factor`/Cover-Mathematik, nur über `OwnerPeriodInput` statt `LeasePeriodInput`. **[ENTSCHEIDUNG E4-4, §4.6]** ob bei unterjährigem Eigentümerwechsel **ein** Lauf je Eigentümer-Periode oder **ein** Lauf mit beiden Adressatenzeilen entsteht (analog M3-E3-3).
- **Miteigentum (mehrere `owner_profile`s je Einheit gleichzeitig):** `ownership_history` erlaubt mehrere gleichzeitige Eigentümer je Einheit mit `mea_share` (Modell-Kommentar bestätigt: „Multiple co-owners per unit at the same time ARE allowed"). Der MEA-Anteil der **Einheit** wird zunächst als Gewicht in der objektweiten Verteilung verwendet; der Einheit-Anteil wird dann **nach `mea_share`** auf die Bruchteilseigentümer weiterverteilt (zweite, exakte Stufe, dann Largest-Remainder). Das ist die **eine** zusätzliche Stufe ggü. M3 (das für die Mieter-BK Bruchteilseigentum ignorieren durfte, M3 §2.2).

> **[§18/fachlich F4-7] `mea_share`-Summe.** Das Modell erzwingt **nicht** hart, dass `Σ mea_share` je Einheit/Zeitpunkt == Einheit-MEA (Soft-Validierung, real-world-Daten). M4-Engine prüft **defensiv** und eskaliert bei grober Inkonsistenz (z. B. Σ `mea_share` = 0 bei vorhandenem Co-Eigentum), rechnet aber bei plausibler, leicht abweichender Summe normalisiert weiter (dokumentiert). Welche Toleranz fachlich zulässig ist, ist abzunehmen.

### 4.4 Adressatenmodell für die Eigentümer-Verteilung (Engine-Spiegelung von M3)

Die zentrale Erkenntnis: `distribute_position` erzeugt **bereits heute** für jede Position alle (unit, time-slice)-Zellen über das ganze Objekt und kennt **Eigentümerzellen** (Leerstand → `vacancy_attribution` mit `owner_profile_id`). Für M4 verschiebt sich der **Adressatenfokus**:

- **M3 (Mieter-BK):** „Greife die Zellen des **Ziel-Leases** heraus; attribuiere Leerstandszellen an Eigentümer."
- **M4 (WEG-Jahresabrechnung):** „Verteile jede Position über alle **Eigentümer**-Zeiträume (`ownership_history`-Cover je Einheit) nach `owner_distribution_key`; summiere je `owner_profile`."

Das verlangt **keinen** neuen Verteilungsalgorithmus, sondern eine **zweite Cover-Funktion** und einen **zweiten Orchestrator**:
- `build_owner_cover(unit, period, ownership_history)` — analog `build_unit_cover`, aber die Slices sind **Eigentümer-Perioden** (kein Tenant/Vacancy-Split; bei Co-Eigentum mehrere gleichzeitige Slices mit `mea_share`). Tagegenau, lückenlos (eine Einheit hat in der WEG immer einen Eigentümer — eine echte Eigentums-„Lücke" wäre ein Datenfehler → Eskalation).
- `compute_weg_jahresabrechnung(inp)` — verteilt, summiert je Eigentümer, bildet Subtotals (umlagefähig/nicht/Rücklage), zieht Soll-Vorschuss ab → Abrechnungsspitze; baut Rücklagen-Entwicklung.

Neue optionale Spalte `settlement_line.addressee_owner_profile_id` `UUID NULL` FK→`owner_profile` (`RESTRICT`) markiert die Eigentümer-Zugehörigkeit einer Zeile (NULL bei objektweiten Zeilen und bei BK).

### 4.5 [§18/fachlich F4-3] Eigentümer-Sphäre: gross_amount vs. Mieteranteil

Für umlagefähige/gemischte Kostenarten verteilt die **Eigentümer**abrechnung i. d. R. den **`gross_amount`** (der Eigentümer trägt die Gesamtkosten anteilig nach MEA; ob/wie viel er als Vermieter auf seinen Mieter umlegen kann, ist die separate Mieter-Sphäre M3). M4 nutzt **nicht** `apportionable_tenant_amount` für die Eigentümer-Verteilung. Bei `GEMISCHT_PRUEFEN` (Hausmeister) trägt die WEG die Gesamtkosten; die Aufspaltung umlagefähig/nicht-umlagefähig ist für die **Mieter**-BK relevant, nicht für die WEG-interne MEA-Verteilung. **Diese Annahme ist fachlich abzunehmen** (Sonderfälle: vereinbarte abweichende Kostentragung, § 16 II WEG).

### 4.6 [ENTSCHEIDUNG E4-4] Ein Lauf je Eigentümer vs. ein Lauf mit allen Eigentümern

**Empfehlung: ein `WEG_JAHRESABRECHNUNG`-Lauf je WEG (objektweit), mit je-Eigentümer-Zeilen** (`addressee_owner_profile_id`). Begründung: Die WEG-Jahresabrechnung ist fachlich **ein** Gesamtdokument für die Gemeinschaft (Gesamtkosten, Gesamtverteilung, Rücklagen-Entwicklung), aus dem die **Einzelabrechnung** je Eigentümer als Sicht/PDF erzeugt wird. Das unterscheidet sich bewusst von M3 (dort: ein Lauf **je Mieter**, weil jeder Mieter ein separates, ihm zuzustellendes Dokument erhält und es kein „Gesamt-BK-Dokument" gibt). Bei **unterjährigem Eigentümerwechsel** erhält die Einheit zwei Eigentümer-Zeilengruppen (Verkäufer/Käufer, tagegenau) im selben Lauf. Das je-Eigentümer-PDF filtert auf `addressee_owner_profile_id`. **Bestätigen.**

> Konsequenz fürs Schema: `settlement_run` für `WEG_JAHRESABRECHNUNG` hat `unit_id`/`lease_agreement_id`/`tenant_profile_id` = NULL (objektweit); die Eigentümer-Zuordnung lebt in `settlement_line.addressee_owner_profile_id`.

---

## 5. Wirtschaftsplan — vorwärtsgerichtet

### 5.1 Eingaben (SDD §10)

- **Geplante Kosten je Kategorie**: entweder **Vorjahres-Ist** (Summe der `cost_entry` der Kategorie im Vorjahr, als Vorschlag) oder **manueller Planwert**. M4 bietet beides; der maßgebliche Wert ist der bestätigte Planwert je `economic_plan_line(line_type='PLANNED_COST')`.
- **Geplante Rücklagenzuführung** (`economic_plan.reserve_contribution_total`) — getrennt, keine Kostenumlage (§3).
- **Verteilerschlüssel je Kostenart** (i. d. R. MEA; kategorie-spezifisch möglich, §4.2).

### 5.2 Ausgaben (SDD §10)

- **Planbetrag je Kategorie** (`PLANNED_COST`-Zeilen, + Subtotals umlagefähig/nicht/Rücklage).
- **Anteil je Einheit/Eigentümer** (`OWNER_ADVANCE`-Zeilen, nach MEA).
- **monatlicher Hausgeldvorschlag** je Eigentümer (`monthly_amount`, §5.3).
- **Anteil umlagefähiger/nicht-umlagefähiger Kosten** und **Rücklagenanteil separat** (über `cost_group` + `RESERVE_CONTRIBUTION`).

Der Wirtschaftsplan ist **kein** WEG-Beschluss — er erzeugt Vorschläge/Unterlagen für die Beschlussfassung (SDD §10); der Beschluss wird in M8 (Beschluss-Sammlung) dokumentiert.

### 5.3 [ENTSCHEIDUNG E4-3] Maßstab der Vorschüsse — monatsbasiert vs. tagegenau

**ADR-0004 hat das ausdrücklich offen gelassen:** „Für **geplante** monatliche Hausgeldvorschüsse (nicht Ist-Kostenverteilung) kann fachlich ein monatsbasierter Maßstab gewünscht sein (12 gleiche Monatsraten). Das ist eine **andere** Größe als die Ist-Kostenverteilung und wird in M4 separat entschieden." M4 entscheidet es jetzt:

**Empfehlung: monatsbasiert für den Plan (12 gleiche Monatsraten), tagegenau bleibt der Ist-Kosten-Maßstab.**

```
Jahresvorschuss_Eigentümer = (Σ geplante Kosten + geplante Rücklagenzuführung) * MEA-Anteil
monatlicher Hausgeldbetrag = Jahresvorschuss_Eigentümer / 12   (kaufmännisch gerundet, Cent-genau)
```

Begründung: Hausgeld ist eine **monatlich gleichbleibende, im Voraus zu zahlende Rate** — der praktische und fachlich erwartete Maßstab sind **12 gleiche Monatsraten**, nicht tagegenaue Tagesbeträge (anders als die rückblickende Ist-Kostenverteilung der Jahresabrechnung, die tagegenau bleibt — ADR-0004). Der unterjährige Eigentümerwechsel betrifft den **Plan** nicht (der Plan plant für die Einheit; wer in welchem Monat zahlt, ist eine Zahlungs-/Übergangsfrage M6, ggf. notarielle Kostenregelung beim Verkauf). Die **12-Raten-Rundung** läuft über dieselbe `distribute_amount`-Disziplin (Largest-Remainder über 12 Raten, damit Σ Monatsraten == Jahresvorschuss exakt — kein Cent-Drift über das Jahr).

> **[ENTSCHEIDUNG E4-3]** Plan-Vorschüsse **monatsbasiert** (12 gleiche Raten, empfohlen) vs. tagegenau. ADR-0007 hält die Entscheidung + Begründung + die explizite Trennung „Plan = monatsbasiert / Ist-Abrechnung = tagegenau" fest. Bestätigen.

### 5.4 Wirtschaftsplan-Snapshot

`economic_plan.snapshot_payload` ist kanonisch wie der M3-Payload: eingefrorene Plan-Eingaben (geplante Kosten je Kategorie, Rücklagenzuführung, Schlüssel, MEA-Basis je Einheit) + Ergebnis (je-Eigentümer-Jahres-/Monatsvorschuss). Gehasht (`snapshot_sha256`) → ein beschlossener/finalisierter Plan ist reproduzierbar und unveränderbar (gleiche Mechanik wie M3 §1.8).

---

## 6. Vermögensbericht (§ 28 IV WEG) — MVP-Umfang [§18/fachlich, nicht eigenmächtig]

### 6.1 Was § 28 IV WEG verlangt

Seit der WEG-Reform muss der Verwalter **nach Ablauf des Kalenderjahres** einen **Vermögensbericht** erstellen, der enthält:
1. den **Stand der Erhaltungsrücklage**, und
2. eine **Aufstellung des wesentlichen Gemeinschaftsvermögens** (im Wesentlichen: Bankkonten-Stände, Forderungen und Verbindlichkeiten der Gemeinschaft).

SDD §11 bestätigt: „Der Vermögensbericht enthält **mindestens** den Stand der Erhaltungsrücklage sowie eine Aufstellung des wesentlichen Gemeinschaftsvermögens (Forderungen/Verbindlichkeiten der Gemeinschaft)."

### 6.2 Was M4 jetzt sauber liefern kann vs. was manuell/später bleibt

Hier ist **strikte Abgrenzung** geboten — der aktuelle Datenstand (M0–M3) bestimmt, was automatisch geht. **Ich entscheide den fachlichen Umfang nicht eigenmächtig** (SDD §18, ADR-0008):

| Bestandteil (§ 28 IV) | Datenlage heute | M4-MVP-Behandlung |
| --- | --- | --- |
| **Stand der Erhaltungsrücklage** | **Vorhanden/ableitbar** (§3.3: Anfangsbestand + Zuführungen − Entnahmen = Endbestand; Zuführungen aus `ERHALTUNGSRUECKLAGE`-`cost_entry`s). | **Geht jetzt** — aus der Rücklagen-Entwicklung (§3.3). Anfangsbestand/Entnahmen in M4 manuell (F4-4/F4-5). |
| **Bankkonten-Stände** | **`bank_account` existiert als M1-Anker, hat aber KEINE Salden-/Buchungsspalten** (Modell-Kommentar: „model anchor: schema + FK only; full implementation in M6"). Kein Kontoauszugsimport (SDD Nicht-Ziel). | **Manueller Stichtagssaldo je Konto** in M4 (Eingabeweg §6.3); echte Salden mit Buchungslogik erst **M6**. **[§18/fachlich F4-8]** |
| **Forderungen** (z. B. Hausgeldrückstände) | **Nicht modelliert** (offene Posten = `OpenItem` ist M6/M7). | **Manuelle Eingabe** oder **offen** in M4 (§6.4). **[§18/fachlich F4-9]** |
| **Verbindlichkeiten** (z. B. offene Lieferantenrechnungen) | **Nicht direkt modelliert** (eine `cost_entry` ist eine Kostenposition, kein Zahlungsstatus; Zahlungen M6). | **Manuelle Eingabe** oder **offen** in M4 (§6.4). **[§18/fachlich F4-9]** |

### 6.3 Eingabeweg für Bankkonten-Stände in M4 (Vorschlag, nicht eigenmächtig final)

`bank_account` hat keine Saldenspalte. Zwei saubere Optionen, **ohne** M6 vorwegzunehmen:

- **(A) Stichtagssaldo im Vermögensbericht-Snapshot (empfohlen für M4-MVP):** Der Sachbearbeiter erfasst beim Anlegen des `VERMOEGENSBERICHT`-Laufs je `bank_account` einen **manuellen Stichtagssaldo** (zum Periodenende). Dieser wird in den `snapshot_payload` eingefroren und als `BANK_BALANCE`-Zeile ausgewiesen. **Kein** Schema-Anbau an `bank_account` (das gehört zur M6-Buchungslogik). Vorteil: greift M6 nicht vor; der Bericht bleibt ein eingefrorener Stichtagsbeleg.
- **(B) Saldenspalte/Bewegungstabelle an `bank_account`:** verfrüht — das ist M6-Scope (Zahlungen/Mietkonto). **Nicht** in M4.

> **[§18/fachlich F4-8]** Eingabeweg Bankkonten-Stände: Empfehlung (A) manueller Stichtagssaldo im Bericht-Snapshot. Fachlich abzunehmen, ob ein manuell erfasster Stichtagssaldo für § 28 IV im MVP genügt (er ist eine Verwalter-Angabe ohne automatischen Kontoabgleich — der Bankimport ist explizit Nicht-Ziel Phase 1, SDD §3). ADR-0008.

### 6.4 Forderungen/Verbindlichkeiten in M4

**[§18/fachlich F4-9] — nicht eigenmächtig entschieden.** Da weder offene Posten (`OpenItem`, M6/M7) noch Zahlungsstatus von Lieferantenrechnungen (M6) modelliert sind, kann M4 Forderungen/Verbindlichkeiten **nicht automatisch** ermitteln. Optionen, klar zur fachlichen Entscheidung gestellt:
- **(a) Manuelle Erfassung** als freie Positionen im Vermögensbericht-Lauf (`RECEIVABLE`/`PAYABLE`-Zeilen, in den Snapshot eingefroren) — der MVP-pragmatische Weg.
- **(b) Bewusst auf später (M6/M7)** verschieben und im Vermögensbericht einen **klar markierten Platzhalter** („Forderungen/Verbindlichkeiten: in dieser Ausbaustufe manuell zu ergänzen / folgt mit M6") ausweisen, statt einen unvollständigen Automatismus vorzutäuschen.

**Empfehlung (zur Bestätigung):** **(a) manuelle Erfassung** im Bericht-Snapshot für den MVP, mit deutlichem Hinweis im PDF, dass diese Werte **Verwalter-Angaben** sind und kein automatischer Forderungs-/Verbindlichkeits-Saldo aus Buchungen. So ist § 28 IV formal bedienbar, ohne Korrektheit vorzutäuschen, die die Daten nicht hergeben. **Fachliche Abnahme erforderlich**, ob das für den MVP genügt.

### 6.5 Vermögensbericht-Datenmodell

Als `settlement_run`-`run_type='VERMOEGENSBERICHT'` (§1.1), objektweit (`unit_id` etc. NULL), mit Bestands-`settlement_line`s (additive `line_type`-Werte, §3.2/§6): `RESERVE_OPENING`/`RESERVE_CONTRIBUTION`(Σ)/`RESERVE_WITHDRAWAL`/`RESERVE_CLOSING`, sowie für den Bericht: `BANK_BALANCE`, `RECEIVABLE`, `PAYABLE` (in derselben CHECK-Erweiterung). Der `snapshot_payload` trägt die vollständige, eingefrorene Aufstellung. **Keine** Soll-Ist-Logik (kein `balance`).

> **[§18/fachlich F4-10] Stichtag/Periode des Vermögensberichts.** § 28 IV: „nach Ablauf des Kalenderjahres". M4 nimmt den **Stichtag = `period_end`** (i. d. R. 31.12.). Ob ein abweichendes Wirtschaftsjahr zulässig/üblich ist, fachlich abzunehmen.

---

## 7. Mischobjekt — Eigentümer-Seite (M4) und Mieter-Seite (M3) sauber trennen

Ein Mischobjekt (`property.management_type='MISCH'`) bleibt **ein** Objekt; dieselbe Einheit erzeugt **beide** Abrechnungen **unabhängig** (SDD §5):

- **Mieter-Seite (M3, fertig):** je vermietete Einheit ein `BK_MIETER`-Lauf je Lease (nur umlagefähige Kosten, `tenant_distribution_key`, `apportionable_tenant_amount`). Leerstand → Eigentümer-Restposten (M3 `vacancy_attribution`).
- **Eigentümer-Seite (M4):** **ein** `WEG_JAHRESABRECHNUNG`-Lauf für das ganze Objekt (alle Kostengruppen, `owner_distribution_key`/MEA, Abrechnungsspitze, Rücklage), je-Eigentümer-Zeilen.

**Trennungsdisziplin (Invariante I4-M):** Dieselbe `cost_entry` fließt in **beide** Sphären, aber über **getrennte** Felder und **getrennte** Engine-Modi:
- Mieter-Sphäre liest `tenant_distribution_key` + `tenant_apportionable` (+ ggf. `apportionable_tenant_amount`) → M3-Engine.
- Eigentümer-Sphäre liest `owner_distribution_key` (+ `cost_group`/`reserve_relevant`) → M4-Engine.
- Eine `NICHT_UMLAGEFAEHIG`-Position (z. B. Verwalterkosten) erscheint in der WEG-Abrechnung (Eigentümer), **nie** in der Mieter-BK — das ist bereits M3-Invariante I-F und wird in M4 gespiegelt: Rücklagenzuführung und nicht-umlagefähige Kosten **nie** in `BK_MIETER`-Zeilen.

Es entsteht **keine** künstliche Aufspaltung des Objekts; die beiden Läufe sind unabhängige `settlement_run`s mit unterschiedlichem `run_type`, beide vom selben Objekt, beide aus denselben `cost_entry`s, aber über disjunkte Felder.

---

## 8. PDF-Outputs (WeasyPrint-Pipeline)

Drei neue PDFs über die in M0 angelegte, in M3 bewährte **WeasyPrint**-Pipeline (`app/core/pdf.py`, `render_pdf`). **Strikt getrennt von der Engine** (SDD §16): die PDF-Schicht liest nur das **finalisierte** Ergebnis (Lines + Snapshot/Display-Namen), rechnet **nichts** neu (Test: PDF-Werte == Line-Werte, wie M3 `settlement_pdf`).

### 8.1 Hausgeld-/Jahresabrechnung (je Eigentümer)
Formelle WEG-Anforderungen (SDD §11): Objekt + Abrechnungszeitraum; Eigentümer + Einheit(en); **Gesamtkosten je Kostenart** (objektweit); **angewandter Schlüssel** je Position; **MEA-Anteil** des Eigentümers je Kostenart; **getrennte Subtotals** umlagefähig/nicht umlagefähig/Rücklage; **Soll-Vorschüsse** (Plan); **Abrechnungsspitze** (Nachschuss/Guthaben, beschlussrelevant, hervorgehoben); **Rücklagen-Entwicklung**; Snapshot-Prüfsumme/Engine-Version (wie M3). Hinweis, dass Beschlussgegenstand die Abrechnungsspitze ist (WEMoG).

### 8.2 Wirtschaftsplan
Planbetrag je Kategorie (+ Subtotals umlagefähig/nicht/Rücklage); je-Eigentümer-Jahresvorschuss + **monatlicher Hausgeldbetrag**; Rücklagenzuführung separat; Hinweis „Vorschlag/Unterlage für die Beschlussfassung, kein Beschluss" (SDD §10).

### 8.3 Vermögensbericht
Stand der Erhaltungsrücklage (mit Entwicklung); Bankkonten-Stände (Stichtag); Forderungen/Verbindlichkeiten (manuell, mit Herkunfts-Hinweis, §6.4); Stichtag = `period_end`. **Klar markierter Hinweis**, welche Werte manuelle Verwalter-Angaben sind (keine automatische Buchungsherleitung) — Transparenz statt vorgetäuschter Korrektheit.

### 8.4 [§18/fachlich F4-11] Exakte Layouts + § 35a EStG
Wie in M3 (O3-4): die **exakten PDF-Layouts** für alle drei WEG-Dokumente sind eine [§18/fachlich]-/Produkt-Frage (SDD §18 nennt „Exakte PDF-Struktur … Hausgeldabrechnung und Vermögensbericht"). M4 stellt **alle Daten** strukturiert bereit; das finale Layout (inkl. evtl. **§ 35a EStG-Ausweis** haushaltsnaher Dienstleistungen/Handwerkerleistungen je Eigentümer — SDD §18 explizit offen) ist fachlich abzustimmen. **Provisorisch** ein funktionales, WEG-vollständiges Standardlayout ohne § 35a-Ausweis; der § 35a-Ausweis ist ein abgegrenzter, fachlich abzunehmender Zusatzbaustein.

---

## 9. Synthetische Golden-Tests (objektive M4-Messlatte)

Es gibt **keine** realen WEG-Beispielabrechnungen. Prüffälle sind **synthetisch** aus den Rechtsregeln + **handberechneten** Soll-Ergebnissen (jeder Cent), exakt wie M3 §9. Beide Test-Arten sind verbindlich für die M4-Abnahme; sie laufen gegen die **reine Engine** (höchste Testdichte) und gegen den Service/PDF.

### 9.1 Golden-Tests (handberechnete Soll-Ergebnisse)

1. **Trivial MEA-Verteilung:** WEG, 3 Einheiten (MEA 500/300/200 von 1000), 1 nicht-umlagefähige Kostenart (Verwalterkosten 3000,00 €/Jahr, MEA), ganzjährig, kein Wechsel. Soll: 1500/900/600 €. Prüft Eigentümer-MEA-Schlüssel + Σ == Gesamt.
2. **Abrechnungsspitze (Kern, handberechnet):** WEG, 2 Einheiten (MEA 600/400), Ist-Kosten gesamt 10.000,00 € (MEA → 6000/4000); Wirtschaftsplan-Soll-Vorschüsse 5500/4200. Soll: Spitze Eigentümer A = 6000 − 5500 = **+500,00 € Nachschuss**; B = 4000 − 4200 = **−200,00 € Guthaben**. Prüft §2.1 exakt (Ist − Soll, **nicht** Ist − gezahlt).
3. **Trennung umlagefähig/nicht/Rücklage:** WEG mit je einer Kostenart aus jeder Gruppe (Gartenpflege `UMLAGEFAEHIG`, Verwalter `NICHT_UMLAGEFAEHIG`, Erhaltungsrücklage-Zuführung `reserve_relevant`). Soll: drei getrennte Subtotals je Eigentümer; Rücklagenzuführung **nicht** in `OWNER_COST_TOTAL`/Spitze. Prüft §3.
4. **Rücklagen-Entwicklung:** Anfangsbestand 20.000,00 €, Zuführung 6.000,00 €, Entnahme 8.000,00 € (Instandsetzung) → Endbestand 18.000,00 €. Prüft Invariante Anfang + Zuführung − Entnahme == Ende; Eskalation bei künstlich inkonsistentem Endbestand.
5. **Eigentümerwechsel unterjährig (tagegenau):** Einheit verkauft 30.06.→01.07. (2025, kein Schaltjahr), eine MEA-Kostenart ganzjährig. Soll: Verkäufer 181/365, Käufer 184/365 des Einheit-Anteils; Σ = Einheit-Anteil (kein Phantom-Tag, ADR-0004/0006-Spiegelung). Prüft §4.3.
6. **Miteigentum:** Einheit mit zwei Bruchteilseigentümern (`mea_share` 0,6 / 0,4), MEA-Kostenart. Soll: Einheit-Anteil exakt nach 0,6/0,4 weiterverteilt, Largest-Remainder summengenau. Prüft §4.3 zweite Stufe.
7. **Mischobjekt-Doppelausweis (Exit-nah):** Mischobjekt; dieselbe `cost_entry`-Menge erzeugt **unabhängig** einen `BK_MIETER`-Lauf (vermietete Einheit, nur umlagefähig) **und** einen `WEG_JAHRESABRECHNUNG`-Lauf (alle Einheiten/Eigentümer, alle Gruppen, Spitze, Rücklage). Prüft §7: Verwalterkosten **nur** in WEG, nie in BK; Σ-Konsistenz beider Sphären.
8. **Wirtschaftsplan → Monatsraten:** geplante Kosten 11.999,00 € + Rücklagenzuführung 1,00 € = 12.000,00 €, MEA 600/400 → Jahresvorschuss 7200/4800 → Monatsraten 600,00/400,00 €; und ein „krummer" Fall (Jahresvorschuss 7201,00 € → 12 Raten via Largest-Remainder, Σ Raten == 7201,00 €). Prüft §5.3 + ADR-0007-Maßstab.
9. **Snapshot/Hash-Determinismus:** Szenario 2 + 7 zweimal finalisiert (identische Eingaben) → identischer `snapshot_sha256`. Prüft M3 §1.8-Mechanik für M4-Payloads.

Jeder Golden-Test prüft **nicht nur die Endsumme**, sondern die **einzelnen Zeilen-Beträge, Schlüssel-Basiswerte, Subtotals, Spitze und Rücklagen-Entwicklung** gegen die Handrechnung (sonst rutscht ein kompensierender Doppelfehler durch — M3-Disziplin).

### 9.2 Property-based Invarianten (Hypothesis, über zufällige Eingaben)

- **I4-A „Σ Eigentümeranteile == Gesamtkosten":** je Position `Σ over owners distribute_amount(...) == G` exakt (ADR-0005, gespiegelt auf Eigentümer).
- **I4-B „MEA-Summen schlüssig":** Σ der verwendeten MEA-Gewichte == Gesamt-MEA-Basis; bei Co-Eigentum `Σ mea_share` einer Einheit konsistent zur Einheit-MEA (innerhalb Toleranz, F4-7).
- **I4-C „Abrechnungsspitze == Ist − Soll":** für zufällige Ist-Kosten + Plan-Soll gilt je Eigentümer `Spitze == owner_cost_total − plan_advance` exakt; **und** `Σ Spitzen == Σ Ist-Kosten − Σ Soll-Vorschüsse` (keine Drift über Eigentümer).
- **I4-D „Rücklagen-Entwicklung konsistent":** `Endbestand == Anfangsbestand + Σ Zuführung − Σ Entnahme` exakt; Rücklagenzuführung erscheint **nie** in `OWNER_COST_TOTAL`.
- **I4-E „Eigentümer-Zeitanteile == 1":** für zufällige `ownership_history`-Zerlegungen einer Einheit summieren die Eigentümer-Zeitfaktoren über den Zeitraum exakt auf 1 (ADR-0004, gespiegelt).
- **I4-F „Rücklage/Nicht-Umlagefähiges nie in Mieter-BK":** in keinem `BK_MIETER`-Lauf erscheint eine `RESERVE_*`- oder `NICHT_UMLAGEFAEHIG`-Position (Spiegelung M3 I-F, sichert die Sphärentrennung §7).
- **I4-G „Plan-Monatsraten summengenau":** `Σ über 12 Monatsraten == Jahresvorschuss` exakt je Eigentümer.
- **I4-H „Determinismus" + „Konsistenz Payload ↔ Lines":** zweimal gleiche Eingabe → identischer kanonischer Payload + Hash; Σ/Werte `snapshot_payload.result` == Σ/Werte `settlement_line`.

Diese Invarianten gelten für **jede** gültige Eingabe (nicht nur die Beispiele) und sind die objektive, modellunabhängige Messlatte.

---

## 10. Implementierungs-Checkliste für `engine-correctness` (M4 baut wegen Korrektheit `engine-correctness`)

> M4 ist laut Roadmap-Modellzuordnung **`engine-correctness` (Opus 4.8)** — die Abrechnungsspitze und Eigentümer-Verteilung sind korrektheitskritisch. Reihenfolge wie M3: **Engine zuerst (reine Funktionen + Tests), dann Persistenz/Snapshot, dann Service, dann API, zuletzt PDF.** **Voraussetzung:** der Mensch hat die [ENTSCHEIDUNG]en (§11) und ADR-0007/0008 bestätigt. Vorher kein Commit. **Alle 227 bestehenden Tests bleiben grün** (additiv).

### 10.1 Engine als reine Funktionen (zuerst, höchste Testdichte)
- [ ] `app/engine/types.py` erweitern: `OwnerCover`/`build_owner_cover`-Eingaben; `WegSettlementInput` (objektweit, `cost_positions` mit `owner_distribution_key`, `reserve_relevant`/`cost_group` je Position, `owner_periods` mit `mea_share`, Plan-Soll-Vorschüsse je Eigentümer, Rücklagen-Anfangsbestand/Entnahmen); `WirtschaftsplanInput`; `VermoegensberichtInput`. **Wiederverwenden**, nicht duplizieren: `UnitInput`, `OwnerPeriodInput`, `distribute_amount`, `time_factor`, `CoverPeriod`-Konzept.
- [ ] `app/engine/periods.py`: `build_owner_cover(unit_id, period, ownership_history)` — tagegenaue Eigentümer-Slices (Co-Eigentum: mehrere gleichzeitige Slices mit `mea_share`), lückenlos (Eigentums-Lücke → Eskalation, kein Vacancy-Konzept hier). Tests I4-E + tag-inklusive Wechsel (Spiegelung der M3-Edge-Cases).
- [ ] `app/engine/weg.py` (neu): `compute_weg_jahresabrechnung(inp) -> WegResult` — verteilt jede Position über Eigentümer-Cover nach `owner_distribution_key` (zwei Stufen bei Co-Eigentum), summiert je Eigentümer, bildet Subtotals (`cost_group`), behandelt `reserve_relevant` als Rücklagenzuführung (nicht Kosten), zieht Plan-Soll ab → **Abrechnungsspitze**, baut Rücklagen-Entwicklung. **Validierung fail-loud** (Eigentums-Lücke, fehlende MEA, inkonsistente Rücklage, fehlendes Plan-Soll). Nutzt `distribute_amount` (ADR-0005).
- [ ] `app/engine/wirtschaftsplan.py` (neu): `compute_wirtschaftsplan(inp) -> PlanResult` — geplante Kosten + Rücklagenzuführung → je-Eigentümer-Jahresvorschuss (MEA) → **12 Monatsraten** (Largest-Remainder, E4-3). Tests I4-G.
- [ ] `app/engine/vermoegensbericht.py` (neu): `build_vermoegensbericht(inp) -> AssetReportResult` — Rücklagen-Entwicklung + (manuelle) Bankkonten-Stände + (manuelle) Forderungen/Verbindlichkeiten zu Bestands-Zeilen. Keine Verteilungsmathematik; nur Aggregation + Invariante-Check (Rücklage).
- [ ] `app/engine/snapshot.py` erweitern: `build_weg_snapshot_payload`, `build_plan_snapshot_payload`, `build_asset_report_payload` (kanonisch, Geld/Quoten als String, sortiert — **wiederverwenden** `_m`/`_q` + `canonical_json`/`sha256_hex`). `run_type`/Dokumenttyp im Payload.
- [ ] Golden-Tests (§9.1) `tests/engine/test_golden_weg_*.py` (handberechnet); Property-based (§9.2) `tests/engine/test_invariants_weg.py` (Hypothesis).

### 10.2 Persistenz & Snapshot (danach)
- [ ] Modelle `app/models/economic_plan.py`, `economic_plan_line.py` (Stil `settlement_run.py`/`settlement_line.py`: Mixins, `Numeric`, `Mapped`, String+CHECK). Geld `Decimal`.
- [ ] **Migration `0012_m4_settlement_line_types`:** additive Erweiterung des `settlement_line.line_type`-CHECK um die neuen Werte (§3.2/§6.5); **neue Spalten** `settlement_line.addressee_owner_profile_id` (FK→`owner_profile`, `RESTRICT`, NULL) und `settlement_run.economic_plan_id` (FK→`economic_plan`, `RESTRICT`, NULL). **`run_type`-CHECK bleibt unverändert** (Werte schon in `0009`). `downgrade()` setzt CHECK/Spalten zurück.
- [ ] **Migration `0013_economic_plan_tables`:** `economic_plan` + `economic_plan_line` (CHECKs, FKs `RESTRICT` zu `organization`, Indizes §1.3/§1.4, `superseded_by_id` Vorwärts). Volles CRUD-Grant für `app_rw` (Trigger-only-Freeze, E3-5-Linie).
- [ ] **Migration `0014_m4_rls_policies`:** vier Policies je neuer Tabelle exakt im Muster `0010` (`NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed).
- [ ] **Migration `0015_m4_immutability_triggers`:** ADR-0003-Trigger (Muster `0011`) für `economic_plan` (Einfrier-Bedingung `status='finalized'`, einzig erlaubte Änderung `superseded_by_id`) und `economic_plan_line` (Eltern-Plan `finalized` → jede Mutation verboten). Trigger-Quelltext ausführlich kommentiert; `downgrade()` droppt Trigger + Funktion. *(Für `settlement_run`/`settlement_line` ist der Trigger bereits in `0011` — die neuen Spalten `economic_plan_id`/`addressee_owner_profile_id` müssen ggf. in die „inhaltstragend, eingefroren ab finalized"-Spaltenliste des bestehenden Triggers aufgenommen werden: **Trigger-Funktion in `0012` per `CREATE OR REPLACE` aktualisieren**, additiv.)*
- [ ] `TENANT_TABLES` in `tests/test_rls_coverage.py` um `economic_plan`, `economic_plan_line` erweitern.
- [ ] Repository `app/repositories/economic_plan_repo.py` (Stil `settlement_repo.py`): Aktiv-Query (`superseded_by_id IS NULL`), Lese-Aggregation (Vorjahres-Ist je Kategorie als Plan-Vorschlag). `SettlementRunRepository` um WEG-Lese-Aggregation erweitern (Eigentümer-/MEA-Daten je Objekt — `ownership_for_property` existiert bereits; ggf. `owner_profiles_for_property` ergänzen).

### 10.3 Service (Snapshot/Finalize atomar)
- [ ] `app/services/weg_settlement_service.py` (oder `SettlementService` um WEG-Pfade erweitern, Stil `settlement_service.py`): `create_weg_draft`, `compute` (WEG-Engine; Plan-Soll-Vorschüsse aus `economic_plan` laden + einfrieren), `set_reserve_inputs` (Anfangsbestand/Entnahmen, F4-4/F4-5), `finalize` (atomar, analog M3), `create_correction_run` (Storno/Neu). **Lese-Grenze `db_exclusive_to_engine_inclusive` für `ownership_history` wie M3** (ADR-0006 — der Helfer ist da).
- [ ] `app/services/economic_plan_service.py`: `create_draft`, `compute` (Plan-Engine), `finalize` (atomar), `create_correction_run`.
- [ ] `app/services/asset_report_service.py` (oder im WEG-Service): `create_draft`, `set_balances`/`set_receivables_payables` (manuell, F4-8/F4-9), `compute`, `finalize`.
- [ ] `audit_event`-Actions: `settlement.weg.*`, `economic_plan.*`, `asset_report.*` (über vorhandenes `AuditRepository`).

### 10.4 API (API-first, RBAC wie M0/M2/M3)
- [ ] `app/api/economic_plans.py`: `POST` (Plan anlegen, `require_write`), `POST /{id}/compute`, `POST /{id}/finalize`, `POST /{id}/correction`, `GET` (Liste/Detail), `GET /{id}/pdf`. READ_ONLY: nur GET. Fremde ID → **404**.
- [ ] WEG-Jahresabrechnung + Vermögensbericht: in `app/api/settlements.py` integrieren (gleiche `settlement_run`-Hülle) — neue Endpunkte/Parameter für `run_type=WEG_JAHRESABRECHNUNG`/`VERMOEGENSBERICHT`, oder eigener Router `app/api/weg_settlements.py`. Router in `app/main.py` registrieren.

### 10.5 PDF (zuletzt, strikt getrennt)
- [ ] `app/templates/weg_jahresabrechnung.{html,css}` + `app/templates/wirtschaftsplan.{html,css}` + `app/templates/vermoegensbericht.{html,css}` und `app/services/*_pdf.py` (Stil `settlement_pdf.py`), die **nur** aus dem finalisierten Lauf/Plan + Lines rendern (`render_pdf`, WeasyPrint). **Keine** Neuberechnung. Tests: PDF-Werte == Line-Werte.

### 10.6 Definition of Done für M4 (Übergabe an M5/M9)
- [ ] Engine-Erweiterung rein (keine DB/HTTP-Importe); `distribute_amount`/`time_factor` **wiederverwendet**, nicht dupliziert; `Decimal` durchgängig.
- [ ] **Golden-Tests §9.1 grün** (handberechnet, inkl. Abrechnungsspitze Szenario 2, Mischobjekt-Doppelausweis Szenario 7).
- [ ] **Property-based Invarianten §9.2 grün** (I4-A bis I4-H), insb. „Σ Eigentümeranteile == Gesamtkosten", „Spitze == Ist − Soll", „Rücklagen-Entwicklung konsistent", „Rücklage/Nicht-Umlagefähiges nie in Mieter-BK".
- [ ] `economic_plan`/`economic_plan_line` mit RLS (vier Policies, fail-closed) + ADR-0003-Trigger (`finalized` unveränderbar) + Snapshot-Hash; bestehender `settlement_*`-Trigger additiv um neue Spalten erweitert.
- [ ] `finalize` atomar (alle drei Dokumenttypen); `audit_event` für alle Vorgänge; Korrektur = neuer Lauf (nie Mutation).
- [ ] WEG-/Plan-/Vermögensbericht-PDF rechnet nichts neu; formelle WEG-Pflichtangaben vollständig.
- [ ] RLS-Coverage-Test + `test_tenant_isolation` um die M4-Tabellen erweitert; **alle 227 bestehenden Tests bleiben grün** (M3-BK unverändert).
- [ ] M4-Exit objektiv erfüllt: Wirtschaftsplan, Jahresabrechnung (mit handberechneter Abrechnungsspitze) und Vermögensbericht für eine WEG sind als PDF erzeugbar; Beträge **konsistent** (Golden + Invarianten).
- [ ] Keine **[ENTSCHEIDUNG]**/**[§18/fachlich]** unmarkiert „weggeraten"; provisorische Annahmen klar gekennzeichnet; fachliche Abnahme (StB/Fachanwalt WEG) empfohlen.

---

## 11. Zusammenfassung der offenen Entscheidungen (für den Menschen)

| # | Punkt | Typ | Empfehlung | Wann |
| --- | --- | --- | --- | --- |
| **E4-1** | Jahresabrechnung + Vermögensbericht als `run_type` derselben `settlement_run`-Hülle | [ENTSCHEIDUNG] | **gleiche Hülle** (M3-E3-6-Linie; Mechanik fertig) | vor M4-Impl |
| **E4-2** | Wirtschaftsplan als eigene Entität `economic_plan`/`economic_plan_line` | [ENTSCHEIDUNG] | **eigene Entität** (SDD §11/§13), gleiche Snapshot-/Trigger-/RLS-Mechanik | vor M4-Impl |
| **E4-3** | Maßstab Plan-Vorschüsse: monatsbasiert (12 Raten) vs. tagegenau | [ENTSCHEIDUNG] | **monatsbasiert** (Ist-Abrechnung bleibt tagegenau, ADR-0004) | vor M4-Impl (ADR-0007) |
| **E4-4** | WEG-Jahresabrechnung: ein Lauf je WEG (je-Eigentümer-Zeilen) vs. ein Lauf je Eigentümer | [ENTSCHEIDUNG] | **ein Lauf je WEG**, objektweit, `addressee_owner_profile_id` | bei Impl |
| **F4-1** | Soll-Quelle wenn kein Plan beschlossen | [§18/fachlich] | provisorisch: Plan erforderlich; sonst fachlich klären | vor produktiv (ADR-0007) |
| **F4-2** | Abrechnungsspitze == Ist − Soll-Vorschuss (nicht − gezahlt) | [§18/fachlich] | so (h. M. WEMoG); Fachanwalt WEG abnehmen | vor produktiv (ADR-0007) |
| **F4-3** | Eigentümer-Sphäre verteilt `gross_amount` (nicht Mieteranteil) | [§18/fachlich] | so; Sonderfälle § 16 II WEG abnehmen | vor produktiv |
| **F4-4/5** | Rücklagen-Anfangsbestand + Entnahmen Datenquelle | [§18/fachlich] | M4 manuell; M6/Buchung später | vor produktiv |
| **F4-7** | `mea_share`-Summen-Toleranz bei Co-Eigentum | [§18/fachlich] | defensiv prüfen, normalisiert rechnen; Toleranz fachlich | vor produktiv |
| **F4-8** | Eingabeweg Bankkonten-Stände (Vermögensbericht) | [§18/fachlich] | manueller Stichtagssaldo im Snapshot (kein M6-Vorgriff) | vor produktiv (ADR-0008) |
| **F4-9** | Forderungen/Verbindlichkeiten im Vermögensbericht | [§18/fachlich] | manuelle Erfassung mit Herkunfts-Hinweis (oder bewusst später) | **fachlich entscheiden** (ADR-0008) |
| **F4-10/11** | Stichtag/Layout/§ 35a EStG-Ausweis | [§18/fachlich] | Daten bereit; Layout + § 35a fachlich/Produkt | vor produktiv |

> **Keine Selbst-Zertifizierung (Haltung):** Dieses Design macht Wirtschaftsplan, Jahresabrechnung, Abrechnungsspitze, Rücklagen-Entwicklung und Vermögensbericht **transparent, deterministisch und nachrechenbar** (Golden-Tests + Property-based Invarianten als objektive Messlatte). Es kann **nicht** garantieren, dass eine konkrete WEG-Abrechnung im Einzelfall gerichts-/prüfungs-/beschlussfest ist. Die fachliche Richtigkeit (insb. WEMoG-Abrechnungsspitze, Trennung umlagefähig/nicht/Rücklage, § 28 IV-Umfang, § 35a EStG) ist vor produktivem Einsatz durch **Steuerberater/Fachanwalt für WEG-Recht** abzunehmen.

---

## 12. Was dieses Dokument bewusst NICHT entscheidet

- **Echte Zahlungen/Mietkonto / Hausgeldkonto-Salden (Ist-Vorschüsse `DERIVED`, Bankkonten-Buchungssalden):** M6 (§2.4, §6.2).
- **Offene Posten / Forderungen-Verbindlichkeiten-Automatik:** M6/M7 (§6.4 — in M4 nur manuell/offen, fachlich abzunehmen).
- **Versammlung / Beschluss-Sammlung** (der Wirtschaftsplan ist Unterlage, kein Beschluss): M8 (SDD §10/§6.10).
- **§ 35a EStG-Ausweis** in Eigentümerabrechnungen: SDD §18, abgegrenzter abnahmepflichtiger Baustein (§8.4).
- **Exakte PDF-Layouts / WEG-Pflichttexte:** SDD §18 (§8.4).
- **CO2-10-Stufen-Eigenberechnung, Gewerbe-Vorwegabzug:** M3-Altlasten (M3 §5/§6), Eigentümer-Seite verteilt `gross_amount` (§4.5).
- **Endgültige Rechtsabnahme:** Steuerberater/Fachanwalt WEG — nicht durch dieses Dokument ersetzbar.
