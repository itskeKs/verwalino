# M6-Design: Zahlungen, Mietkonto & offene Posten

Stand: 2026-06-02 · Version 0.1 (Design, vor Implementierung)
Bezug: [sdd-property-management-mvp.md](sdd-property-management-mvp.md) v0.2 §3 (Nicht-Ziel Bankimport), §5.4 (Zahlungseingänge — *Anm.: SDD nummeriert §6.3/§6.7*), §6.3 (Bankkonten), §6.7 (Miet-/Zahlungsverwaltung), §11 (PDF), §13 (`Payment`/`OpenItem`), §14 (Chronologie), §16, §17, §18 · [roadmap.md](roadmap.md) M6 · [architecture.md](architecture.md) §2.6 (Vermögenstrennung), §5.1 (Konventionen), §5.3 (`bank_account`-Anker), Annahme A-4 · [m3-abrechnungs-engine.md](m3-abrechnungs-engine.md) §7 (`advance_payments_source` MANUAL→DERIVED) · [m4-weg-abrechnung.md](m4-weg-abrechnung.md) §2.4 (paid_advances, Spitze) · ADRs: [0003](adr/0003-gobd-unveraenderbarkeit-mechanik.md) (wiederverwendet), [0005](adr/0005-rundung-largest-remainder.md) (wiederverwendet), [0006](adr/0006-valid-to-konvention.md) (wiederverwendet), [0007](adr/0007-weg-abrechnungsspitze-und-wirtschaftsplan.md) (Maßstab monatlich, Spitze) · **[0009-soll-ist-und-zahlungsbeleg.md](adr/0009-soll-ist-und-zahlungsbeleg.md) (neu)** · **[0010-vermoegenstrennung-zahlungen.md](adr/0010-vermoegenstrennung-zahlungen.md) (neu)**
Autor: `engine-correctness` (Opus 4.8) · Status: **Entwurf zur Bestätigung durch den Menschen — kein Anwendungscode, keine Migration, kein Commit**

> Dieses Dokument spezifiziert **M6** (Zahlungen, Mietkonto, offene Posten) bis auf die Arithmetik. M6 ist der Punkt, an dem der **Geldfluss** real wird und die in M3/M4 vorbereitete Brücke `advance_payments_source: MANUAL → DERIVED` geschlossen wird: Abrechnungen sollen die Vorauszahlungssumme **aus erfassten Zahlungen ableiten** können, statt sie manuell zu führen. **Manuell bleibt als Fallback.**
>
> **Verbindlichkeit:** SDD (v0.2), Roadmap, `architecture.md`, `m3-abrechnungs-engine.md`, `m4-weg-abrechnung.md` und die ADRs 0001–0008 sind die Quelle der Wahrheit und bleiben **unangetastet**. Dieses Dokument ist additiv (neues Doc + ADR-0009/0010). Entscheidungen, die der Mensch bestätigen soll, sind **[ENTSCHEIDUNG]**; offene SDD-§18-/fachliche Punkte **[§18/fachlich]** (nicht eigenmächtig final entschieden; provisorische, klar markierte Annahme; fachliche Abnahme empfohlen).
>
> **Manuelle Erfassung — kein Bankimport.** SDD §3 ist hart: „Kein Bankimport (CAMT/MT940) und kein automatischer Zahlungsabgleich." M6 erfasst Zahlungen **ausschließlich manuell**; die einzige „Automatik" ist die **deterministische, reine Ableitung** erfasster Zahlungen in Abrechnungsgrößen (§6) — kein Bank-Parsing, kein Matching gegen Kontoauszüge.
>
> **Keine Selbst-Zertifizierung:** Dieses Design macht Soll, Ist, offene Posten und die Abrechnungs-Anbindung *transparent, deterministisch und nachrechenbar* und legt jede Annahme offen. Es kann **nicht** garantieren, dass das Mietkonto/Hausgeldkonto im konkreten Fall buchhalterisch/prüfungsfest ist (M6 ist ausdrücklich **keine** Finanzbuchhaltung, SDD §1/§3). Die fachliche Richtigkeit (Mietkonto-Tiefe, Kaution, GoBD-Belegqualität manueller Zahlungen, Treuhandtrennung) ist vor produktivem Einsatz durch **Steuerberater/Fachanwalt** abzunehmen.

---

## 0. Geltungsbereich, Exit-Kriterium, Anschluss an M0–M5

**M6-Exit (Roadmap):** „Soll-Ist je Person stimmt; **eine Abrechnung zieht erfasste Zahlungen automatisch**." Das ist die objektive Messlatte. „Soll-Ist stimmt" wird durch die **synthetischen Golden-Tests** (§7) mit handberechneten Soll-/Ist-/Saldo-Werten belegt; „Abrechnung zieht Zahlungen automatisch" durch einen Golden-Test, der eine **DERIVED**-Vorauszahlung in einer BK-Abrechnung gegen die **manuelle** Variante prüft (Bit-Gleichheit des Ergebnisses, §7.3).

**M6 baut auf einem verifizierten Fundament auf (M0–M5, 291 Tests grün) und übernimmt dessen Konventionen unverändert.** M6 ist **additiv**: es fügt `payment` + Soll-Berechnung + Zuordnung hinzu und schließt einen vorbereiteten Anschluss in M3/M4. Die wichtigste Erkenntnis aus der Code-Durchsicht: **das M3/M4-Fundament ist bereits M6-fähig gebaut.**

| Vorhandenes Fundament (M0–M5, live) | M6 nutzt es so |
| --- | --- |
| `bank_account` (M1-Anker): `organization_id`, `property_id`, `account_type` (`LAUFEND`/`RUECKLAGE`/`KAUTION`), IBAN/BIC/Bank — **keine** Salden-/Buchungsspalten (Modell-Kommentar: „model anchor: schema + FK only; full implementation in M6") | **Drehpunkt der Vermögenstrennung** (ADR-0010). M6 baut **kein** Saldenfeld an `bank_account` an — Salden bleiben **abgeleitet** aus `payment` (§1.1). |
| `settlement_run.advance_payments_total` (`NUMERIC(14,2)`) + `advance_payments_source` (`String(12)`, `MANUAL`\|`DERIVED`, im Immutability-Trigger als eingefrorene Spalte gelistet, Migration `0011`) | **Die vorbereitete Brücke.** M6 setzt `DERIVED` und füllt `advance_payments_total` aus Zahlungen (§6). **Kein Schemabruch** — die Spalten existieren und sind eingefroren-ab-`finalized`. |
| `lease_agreement` (temporal, EXCLUDE pro Einheit, `base_rent`, `advance_payment`, `valid_from`/`valid_to`) | **Soll-Quelle Mieter** (Kaltmiete + NK-Vorauszahlung), monatlicher Maßstab, tagegenaue Anschnitte (§2). |
| `economic_plan_line(line_type='OWNER_ADVANCE')` mit `owner_profile_id`, `unit_id`, `planned_amount` (Jahres), `monthly_amount` (monatlich, ADR-0007 E4-3b) | **Soll-Quelle Eigentümer** (Hausgeld-Vorschuss) aus dem **finalisierten** Plan (§2.2). |
| WEG-Service: `WegComputeExtras.paid_advances: list[ManualOwnerAdvance]` (per Eigentümer, **nachrichtlich**, MANUAL) und `OwnerAdvanceInput(plan_advance_total)` (Soll aus Plan); Spitze = `Ist − Soll(Plan)` (ADR-0007), `paid_advances` **getrennt** | M6 ersetzt die **MANUAL** `paid_advances` durch **DERIVED** aus Zahlungen (§6.3). **Die Abrechnungsspitze bleibt `Ist−Soll(Plan)` — Zahlungen fließen NICHT hinein** (nur nachrichtlicher Zahlungsstand). |
| `cost_entry`-Muster: `status` (`draft`→`confirmed`) + `superseded_by_id` (Vorwärts-Zeiger) + ADR-0003-Trigger (`status='confirmed'` einfriert) + Storno-als-Neuversion | **1:1-Vorbild für `payment`**: `status` (`draft`→`booked`), `reversed_by_id`, ADR-0003-Trigger (`status='booked'` einfriert), Korrektur = Storno + Neubuchung (ADR-0009). |
| RLS-Muster (`ENABLE`+`FORCE`, vier Policies, `NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed, Migration `0010`/`0014`) | `payment` bekommt exakt dieses Muster (Migration analog). |
| `TenantRepository`/`base.py`, atomare Service-Transaktion (`*_service.confirm`/`finalize`), `AuditRepository`, `app/core/temporal.py` (`db_exclusive_to_engine_inclusive`, ADR-0006), `app/engine/rounding.py` (`distribute_amount`), `app/engine/periods.py` (tagegenau) | Vorbilder/Bausteine 1:1 für `PaymentRepository`, `payment_service`, `app/engine/mietkonto.py` (reine Soll/Ist-Funktionen). |
| Geld `NUMERIC(14,2)`/`Decimal`, Quoten `NUMERIC(12,6)`, UUID-PK, `TIMESTAMPTZ`, String+CHECK statt DB-Enum, FK `ondelete=RESTRICT` zu `organization` | Durchgängig übernommen. |
| Migrationen `0001`–`0015` vorhanden | M6 fügt **`0016`–`0019`** additiv hinzu (Reihenfolge §8). |

**Nicht in M6:** Mahnwesen/Mahnstufen/`DunningNotice` (M7 — M6 liefert die *berechneten* überfälligen Posten als Eingabe; die Mahn-Persistenz entscheidet **M7**, nicht M6, ADR-0009 §1/F6-1). Bankimport/Matching (SDD §3 Nicht-Ziel, dauerhaft). Vollwertige Kautionsbuchhaltung mit Verzinsung (§3.4, [§18/fachlich]). Kontoauszugsabgleich.

**Architektur-Leitsatz (SDD §16, hier hart, wie M3/M4):** Die **Soll-/Ist-/Saldo-Arithmetik ist eine Schicht reiner Funktionen** (`Decimal`/`date` ein/aus, keine DB/HTTP/I/O), getrennt von Persistenz, API und PDF. Das ist die Voraussetzung für die testbare, deterministische Ableitung in die Abrechnung (§6) und die Golden-Tests (§7). M6-Logik kommt in dasselbe `app/engine/`-Paket, nicht in Services.

---

## 1. Datenmodell

### 1.1 `bank_account` — jetzt ausbauen oder unverändert lassen?

> **[ENTSCHEIDUNG E6-5]: `bank_account` strukturell unverändert lassen — KEINE Salden-/Buchungsspalten anbauen.** *(Empfehlung)*

Der Anker hat heute `organization_id`, `property_id`, `account_type` (`LAUFEND`/`RUECKLAGE`/`KAUTION`), IBAN/BIC/Bank — und bewusst **keine** Saldenspalte. Das ist richtig und bleibt so:

- **Der Kontostand ist eine abgeleitete Größe**, kein gespeicherter Wert: `saldo(konto) = Σ Eingänge − Σ Ausgänge` über die `payment`-Zeilen dieses Kontos (analog zur abgeleiteten Soll-Philosophie, ADR-0009). Ein gespeicherter Saldo würde von den Buchungen driften (dieselbe Zwei-Wahrheiten-Falle wie beim Soll) und wäre ein zweiter, pflegebedürftiger Zustand.
- **Genau das hat M4 (§6.2) bereits vorausgesehen:** Der Vermögensbericht nutzt heute einen **manuell erfassten Stichtagssaldo** je Konto (ADR-0008), gerade *weil* `bank_account` keine Saldenspalte hat. Ab M6 kann derselbe Bericht den Saldo **optional aus `payment` ableiten** (§6.5) — ohne `bank_account` umzubauen.
- **Eine additive Härtung** ist nötig (nicht für Salden, sondern für die Vermögens-Kohärenz, ADR-0010): ein **UNIQUE-Index `(organization_id, id, property_id)`** auf `bank_account`, damit `payment` per Composite-FK garantiert an das richtige Objekt gebunden ist (§4). Das ist kein „Ausbau" des Kontos, sondern die Voraussetzung der treuhandsicheren Zuordnung.

**Optionale, additive Felder (nicht zwingend M6):** `opening_balance NUMERIC(14,2)` + `opening_balance_date DATE` als **Eröffnungssaldo-Anker** (Kontostand zu einem Stichtag, ab dem `payment`-Bewegungen fortgeschrieben werden). Begründung: ohne Eröffnungssaldo ist der abgeleitete Kontostand nur relativ zu den *im System erfassten* Zahlungen, nicht der reale Banksaldo (Zahlungen vor Systemeinführung fehlen). **[ENTSCHEIDUNG E6-6]** ob M6 diesen Eröffnungssaldo-Anker einführt (empfohlen: ja, klein und additiv) oder ihn nach M9 verschiebt. Er ist **kein** Buchungssatz, sondern eine Verwalter-Stichtagsangabe (wie der Vermögensbericht-Stichtagssaldo, ADR-0008).

### 1.2 `payment` (neue Tabelle, tenant-behaftet, RLS, ab `booked` unveränderbar)

Eine `payment`-Zeile ist **eine erfasste Geldbewegung** (Eingang oder Ausgang) gegen ein Konto, einer Gegenpartei und einem Zweck/Typ zugeordnet. Sie ist ein **Buchungsbeleg** (GoBD, ADR-0009): ab Status `booked` unveränderbar; Korrektur = Storno + Neubuchung.

| Spalte | Typ | Bedeutung / Begründung |
| --- | --- | --- |
| `id` | `UUID` PK | `gen_random_uuid()`, wie überall. |
| `organization_id` | `UUID NOT NULL` FK→`organization` (`RESTRICT`) | I1, RLS. |
| `bank_account_id` | `UUID NOT NULL` FK→`bank_account` (`RESTRICT`) | **Pflicht-Drehpunkt** (ADR-0010): jede Zahlung läuft gegen genau ein Konto. |
| `property_id` | `UUID NOT NULL` FK→`property` (`RESTRICT`) | Objektbezug (Vermögenstrennung). **Gespiegelt aus dem Konto**, per Composite-FK an das Konto gebunden (§4) — nicht frei wählbar. |
| `management_mandate_id` | `UUID NULL` FK→`management_mandate` (`RESTRICT`) | Mandatsbezug (Vermögenstrennung-Anker, architecture.md §2.6). Aus dem Konto/Objekt abgeleitet. |
| **Geldbewegung (I4):** | | |
| `direction` | `String(3) NOT NULL` | `IN` (Eingang) \| `OUT` (Ausgang). CHECK. |
| `amount` | `NUMERIC(14,2) NOT NULL` | **Betrag, immer positiv**; das Vorzeichen trägt `direction` (klarer als signierte Beträge; ein Storno ist eine eigene `IN`/`OUT`-Zeile, kein negativer Betrag). `CHECK (amount > 0)`. |
| `value_date` | `DATE NOT NULL` | Wertstellungs-/Buchungsdatum der Zahlung (maßgeblich für Perioden-Zuordnung, §6). |
| **Gegenpartei (genau eine Rolle):** | | |
| `tenant_profile_id` | `UUID NULL` FK→`tenant_profile` (`RESTRICT`) | bei Mieter-Zahlung. |
| `owner_profile_id` | `UUID NULL` FK→`owner_profile` (`RESTRICT`) | bei Eigentümer-Zahlung. *(genau **eine** der beiden gesetzt bei personenbezogenen Typen; CHECK §1.4.)* |
| **Zweck/Typ (kontrolliertes Vokabular, §1.3):** | | |
| `payment_type` | `String(24) NOT NULL` | `NK_VORAUSZAHLUNG` \| `HAUSGELD_VORSCHUSS` \| `RUECKLAGENANTEIL` \| `KALTMIETE` \| `SONDERZAHLUNG` \| `NACHZAHLUNG` \| ggf. `KAUTION`. CHECK. **Der Typ steuert die Abrechnungs-Anbindung (§6) — er ist korrektheitskritisch.** |
| **Zuordnung (was wird mit der Zahlung beglichen):** | | |
| `lease_agreement_id` | `UUID NULL` FK→`lease_agreement` (`RESTRICT`) | bei mieterbezogenen Typen: der Mietvertrag, dessen Soll die Zahlung bedient. |
| `period_year` | `SMALLINT NULL` | Abrechnungs-/Soll-Jahr, dem die Zahlung zugeordnet ist (für die DERIVED-Aggregation, §6). |
| `period_month` | `SMALLINT NULL` | optionaler Monatsbezug (1–12) bei monatlicher Sollstellung; NULL = ganze Periode/Pauschalzahlung. CHECK 1–12. |
| `settlement_run_id` | `UUID NULL` FK→`settlement_run` (`RESTRICT`) | optional: Zahlung gegen eine konkrete finalisierte Nachzahlung/Guthaben (z. B. `payment_type=NACHZAHLUNG` begleicht das `balance` eines BK-Laufs). |
| **GoBD/Unveränderbarkeit (ADR-0003/0009):** | | |
| `status` | `String(12) NOT NULL DEFAULT 'draft'` | `draft` (Erfassung, editierbar) \| `booked` (verbucht, eingefroren). CHECK. Einfrier-Bedingung: `booked`. |
| `booked_at` | `TIMESTAMPTZ NULL` | Zeitpunkt des Verbuchens. |
| `booked_by` | `UUID NULL` FK→`user` (`SET NULL`) | Wer verbucht hat (Audit). |
| `reverses_id` | `UUID NULL` FK self (`RESTRICT`) | bei einer **Storno-Zahlung**: zeigt auf die stornierte Original-Zahlung. |
| `reversed_by_id` | `UUID NULL` FK self (`RESTRICT`) | bei der **stornierten** Zahlung: zeigt auf die Storno-Zeile (Vorwärts-Zeiger, E-4-Konvention wie `superseded_by_id`). |
| `retention_until` | `DATE NULL` | Aufbewahrungsanker (8 Jahre, architecture.md §3.3 / ADR-0003 #3) — Buchungsbeleg-Aufbewahrung; DSGVO-Verzahnung (§5). |
| `note` | `String(500) NULL` | Verwendungszweck/Notiz (z. B. Originaltext der Überweisung, soweit manuell eingegeben). |
| `created_at`/`updated_at` | `TIMESTAMPTZ` | wie überall. |

Constraints: `CHECK (direction IN ('IN','OUT'))`; `CHECK (amount > 0)`; `CHECK (payment_type IN (...))`; `CHECK (status IN ('draft','booked'))`; `CHECK (period_month IS NULL OR period_month BETWEEN 1 AND 12)`; Gegenpartei-/Typ-Konsistenz-CHECKs (§1.4); **Composite-FK** `(organization_id, bank_account_id, property_id)` → `bank_account(organization_id, id, property_id)` (§4, ADR-0010). **RLS** vier Policies (Muster `0010`). Indizes: `organization_id`; `bank_account_id`; `(property_id, period_year)`; `lease_agreement_id`; `(tenant_profile_id, period_year)`; `(owner_profile_id, period_year)`; `payment_type`; `settlement_run_id`; `reverses_id`/`reversed_by_id`.

### 1.3 `payment_type` — kontrolliertes Vokabular [§18/fachlich, nicht eigenmächtig final]

Der Typ ist der **Steuerschlüssel** für die Abrechnungs-Anbindung (§6) — daher streng als CHECK-Vokabular, nicht freitext. SDD §6.7 listet die Zuordnungs-Zwecke; daraus das M6-Vokabular:

| `payment_type` | Gegenpartei | SDD §6.7 | fließt in (§6) |
| --- | --- | --- | --- |
| `KALTMIETE` | Mieter | „Mieter: Kaltmiete" | Mietkonto-Soll-Ist (Miete); **nicht** in BK-Abrechnung. |
| `NK_VORAUSZAHLUNG` | Mieter | „Mieter: Nebenkostenvorauszahlung" | **BK-Abrechnung `advance_payments_total` (DERIVED)** (§6.2). |
| `HAUSGELD_VORSCHUSS` | Eigentümer | „Eigentümer: Hausgeldvorauszahlung" | WEG-Soll-Ist **nachrichtlich**; **NICHT** in die Abrechnungsspitze (§6.3). |
| `RUECKLAGENANTEIL` | Eigentümer | „Eigentümer: Rücklagenanteil" | Rücklagen-Ist (nachrichtlich); **nicht** in die Spitze (§6.3/§6.4). |
| `SONDERZAHLUNG` | Eigentümer | „Eigentümer: Sonderumlage/Sonderzahlung" | Sonder-Soll-Ist (nachrichtlich); abrechnungsneutral, soweit nicht als Kostenposition erfasst. |
| `NACHZAHLUNG` | Mieter o. Eigentümer | „… / Nachzahlung" | begleicht das `balance` einer **finalisierten** Abrechnung (`settlement_run_id`); abrechnungsneutral für die *nächste* Periode. |
| `KAUTION` *(optional)* | Mieter | „Mieter: Kaution" | Kautionskonto-Bestand; **abrechnungsneutral** (§3.4). |

> **[§18/fachlich F6-3] Umfang der Zahlungsarten.** SDD §18 nennt die Mietkonto-Detailtiefe und Kautionsbehandlung ausdrücklich offen. Ob `KAUTION` in M6 schon geführt wird (eigener Kontotyp existiert: `bank_account.account_type='KAUTION'`) oder nach M9 verschoben wird, und ob weitere Typen nötig sind (z. B. Mietminderung, Rückzahlung Guthaben als eigener `OUT`-Typ), ist **nicht eigenmächtig** entschieden. **Provisorische Annahme:** obiges Vokabular; `KAUTION` als optionaler Typ mitgeführt, aber abrechnungsneutral. Fachlich abzunehmen.

### 1.4 `open_item` — abgeleitet (berechnet), nicht persistiert [ENTSCHEIDUNG]

> **[ENTSCHEIDUNG E6-1]: `open_item` ist ein abgeleitetes (berechnetes) Objekt, KEINE persistierte Tabelle.** *(Empfehlung; vollständige Begründung in ADR-0009.)*

Ein offener Posten ist die **berechnete Saldo-Sicht** je (Person, Periode, Typ):

```
open_item(person, periode, typ) = Soll(person, periode, typ)  −  Σ zugeordnetes Ist(person, periode, typ)
   offen > 0  → Rückstand (Person schuldet noch)
   offen < 0  → Überzahlung / Guthaben
   offen = 0  → ausgeglichen
```

- **Soll** ist selbst abgeleitet (§2): aus `lease_agreement`/`economic_plan`, monatlicher Maßstab. **Ist** ist `Σ amount` der `booked`, **nicht-stornierten**, typ-/perioden-/person-gleichen `payment`-Zeilen.
- `open_item` wird als **reine Funktion** (`app/engine/mietkonto.py`) berechnet und über einen **read-only API-Endpunkt** geliefert (Liste je Person/Objekt). Es entsteht **keine** je Monat materialisierte Zeile, kein Erzeugungsjob, keine zweite Wahrheit, die driften könnte.
- **M7-Abgrenzung (kein Vorgriff):** Mahnstufen/-historie (M7) brauchen einen pro-Posten-Anker (ein Mahn-Status „klebt" an einem konkreten überfälligen Posten). M6 liefert die *berechneten* überfälligen Posten als Eingabe; ob M7 daraus einen kleinen persistierten Mahn-/Status-Datensatz materialisiert (mit Bezug auf Person/Periode/Typ), ist **[§18/fachlich F6-1]** und Sache von M7. **M6 entscheidet die Mahn-Persistenz nicht.**

**Gegenpartei-/Typ-Konsistenz (CHECK auf `payment`):** mieterbezogene Typen (`KALTMIETE`, `NK_VORAUSZAHLUNG`, `KAUTION`) verlangen `tenant_profile_id NOT NULL` und `owner_profile_id NULL`; eigentümerbezogene (`HAUSGELD_VORSCHUSS`, `RUECKLAGENANTEIL`, `SONDERZAHLUNG`) das Umgekehrte; `NACHZAHLUNG` erlaubt beide (je nach Adressat), verlangt aber, dass **genau eine** Person gesetzt ist. Diese CHECKs verhindern, dass eine Zahlung typ-inkonsistent erfasst wird (Grundlage der eindeutigen DERIVED-Zuordnung, §6).

---

## 2. Soll-Generierung — monatlicher Maßstab (konsistent zu ADR-0007)

Das Soll ist **abgeleitet** (ADR-0009) und wird je (Person, Periode) als reine Funktion berechnet. Maßstab **monatlich** — konsistent zu ADR-0007 E4-3b (Hausgeld als 12 gleiche Monatsraten) und zur SDD §6.7 („Soll-Stellung je Periode").

### 2.1 Mieter-Soll (je Mietvertrag)

```
monatliches Soll(lease, monat) = base_rent + advance_payment        (aus lease_agreement)
```

- **Trennung nach Komponente:** Das Soll wird getrennt nach `KALTMIETE` (= `base_rent`) und `NK_VORAUSZAHLUNG` (= `advance_payment`) geführt, denn nur die NK-Vorauszahlung fließt in die BK-Abrechnung (§6.2), die Kaltmiete nicht. Eine Vermischung würde die Abrechnungs-Anbindung verfälschen.
- **Unterjähriger Beginn/Ende (tagegenau angeschnitten):** Beginnt/endet der Mietvertrag mitten im Monat, ist die Soll-Stellung des Anschnittsmonats **anteilig**. Maßstab: monatlich, der Anschnitt tagegenau über dieselbe `app/engine/periods.py`-Arithmetik wie M3 (tag-inklusiv, ADR-0004/0006). **[§18/fachlich F6-4]** ob ein angeschnittener Monat tagegenau (`tage_im_monat_belegt / tage_im_monat`) oder pauschal voll/halb berechnet wird — mietvertraglich/fachlich unterschiedlich gehandhabt. **Provisorische Annahme:** tagegenauer Anschnitt (konsistent zu M3); klar markiert, fachlich abzunehmen.
- **Lese-Grenze:** `lease_agreement.valid_to` ist halboffen gespeichert; die Soll-Funktion liest über `db_exclusive_to_engine_inclusive` (ADR-0006) — **dieselbe** Lese-Grenze wie M3, **eine** Definition. So zählt der letzte Mietmonat korrekt (kein Off-by-one am Vertragsende).
- **Fälligkeitstag:** ein Soll-Monat gilt ab seinem Fälligkeitstag als fällig (für „überfällig", M7). **[§18/fachlich]** Default-Fälligkeit (z. B. 3. Werktag des Monats, § 556b BGB) — provisorisch Monatsanfang; fachlich abzunehmen.

### 2.2 Eigentümer-Soll (je Eigentümer, aus finalisiertem Wirtschaftsplan)

```
monatliches Soll(owner, monat) = economic_plan_line(OWNER_ADVANCE, owner).monthly_amount
                                 (aus dem FINALISIERTEN economic_plan des Jahres)
```

- Die Quelle ist die in M4 bereits berechnete und **monatsbasiert** (ADR-0007 E4-3b) gerundete `OWNER_ADVANCE.monthly_amount` je Eigentümer. M6 erfindet hier **nichts** — es liest den finalisierten Plan.
- **Nur finalisierte Pläne** liefern ein verbindliches Soll (ein `draft`-Plan ist kein beschlossenes Soll). **[§18/fachlich F6-5]** Verhalten ohne finalisierten Plan (analog M4 F4-1): provisorisch — kein Eigentümer-Soll ohne finalisierten Plan; das Hausgeld-Konto zeigt dann nur die Ist-Zahlungen ohne Soll-Abgleich. Fachlich abzunehmen.
- **Mehrere Einheiten je Eigentümer:** Der Plan hat eine `OWNER_ADVANCE`-Zeile je (Eigentümer, Einheit); das Eigentümer-Soll je Monat ist die **Summe** der `monthly_amount` über seine Einheiten (exakt, kein erneutes Runden — Cent + Cent = Cent, m3 §4.3).

### 2.3 Soll-Engine als reine Funktion

`app/engine/mietkonto.py`:
- `tenant_expected_charges(lease, period) -> list[ExpectedCharge]` — monatliche Soll-Positionen (getrennt KALTMIETE/NK_VORAUSZAHLUNG), tagegenaue Anschnitte.
- `owner_expected_charges(owner_advance_lines, period) -> list[ExpectedCharge]` — monatliche Hausgeld-Soll-Positionen je Eigentümer.
- Beide nehmen **eingefrorene Eingaben** (Dataclasses) und liefern eine deterministische Soll-Liste; keine DB. Die Rundung der Monatsraten erfolgt bereits in M4 (`monthly_amount`) bzw. ist beim Mieter-Soll `base_rent+advance_payment` (bereits Cent-genau) × ggf. tagegenauem Anschnitt über `distribute_amount` (ADR-0005, falls ein Anschnitt mehrere Teil-Soll ergibt).

---

## 3. Ist / Zuordnung — Zahlungen den Soll-Positionen zuordnen

### 3.1 Saldo & offene Posten je Person (reine Arithmetik, Decimal)

`app/engine/mietkonto.py`:
```
account_statement(expected_charges, payments) -> MietkontoResult
```
- Gruppiert das **Soll** (aus §2) und das **Ist** (`booked`, nicht-stornierte `payment`) je (Periode, Typ).
- Liefert je Soll-Periode den **offenen Posten** (Soll − zugeordnetes Ist) und den **laufenden Saldo** über die Periode.
- **Reine Decimal-Arithmetik**, kein Float, deterministisch — Golden-getestet (§7). Summenbildung ohne Rundung (alle Beträge bereits Cent-genau).

### 3.2 Teilzahlung → offener Posten

Zahlt der Mieter weniger als das Soll (z. B. Soll 850,00 €, Zahlung 600,00 €), bleibt ein **offener Posten** von 250,00 € (offen > 0). Mehrere Teilzahlungen summieren sich (Σ Ist). Keine automatische „Verrechnungsreihenfolge" über Perioden hinweg im MVP — die Zuordnung folgt der **expliziten** `period_year`/`period_month`/`lease_agreement_id` der Zahlung (manuell erfasst), nicht einer geratenen Tilgungslogik.

> **[§18/fachlich F6-6] Tilgungsreihenfolge / Verrechnung.** Bei mehreren offenen Perioden und einer Zahlung ohne genauen Monatsbezug ist die Verrechnungsreihenfolge (älteste Schuld zuerst? § 366 BGB?) fachlich. **Provisorische Annahme:** Zahlungen tragen ihren Periodenbezug **explizit** (manuell beim Erfassen zugeordnet); keine automatische Tilgungsreihenfolge im MVP. Fachlich abzunehmen, ob eine §-366-BGB-Verrechnung nötig ist.

### 3.3 Überzahlung → Guthaben

Zahlt die Person mehr als das Soll (z. B. Soll 850,00 €, Zahlung 1000,00 €), ist der offene Posten **negativ** (−150,00 €) = **Guthaben**. Das Guthaben erscheint als negativer offener Posten/Saldo; eine **Rückzahlung** an die Person ist eine eigene `payment`-Zeile mit `direction='OUT'` (kein negatives `amount`).

### 3.4 Kaution / Kautionskonto [§18/fachlich]

`bank_account.account_type='KAUTION'` existiert. Kautionszahlungen (`payment_type='KAUTION'`, falls geführt, F6-3) erhöhen den **Kautionskonto-Bestand** (abgeleitet), sind aber **abrechnungsneutral** (keine BK-/WEG-Größe). Verzinsung, Anlageform, Rückzahlung bei Auszug sind **[§18/fachlich]** (SDD §18 nennt Kautionsbehandlung offen) — nicht eigenmächtig; provisorisch nur Bestandsführung, keine Verzinsung im MVP.

---

## 4. Vermögenstrennung — DB-Härtung (Constraint) jetzt [ENTSCHEIDUNG]

> **[ENTSCHEIDUNG E6-4]: DB-Constraint-Härtung der Vermögens-Kohärenz jetzt (Composite-FK), KEINE zweite RLS-Achse.** *(Empfehlung; vollständige Begründung + verworfene Optionen in ADR-0010.)*

M0 hat diese Entscheidung ausdrücklich **nach M6 vertagt** (architecture.md A-4, ADR-0001 §Abgrenzung: „DB-Härtung der Vermögenstrennung frühestens mit M6"). M6 entscheidet sie:

- **Das Risiko ist konkret und treuhandrelevant:** Geld der WEG A darf nie auf dem Konto/in der Sphäre der WEG B landen — **auch innerhalb derselben Organization**. RLS fängt das **nicht** ab (beide WEGs sind dieselbe Org). Reine App-Layer-Disziplin ist hier zu schwach (dieselbe Schwäche, die ADR-0001 für die Mandantentrennung gerade nicht akzeptiert hat).
- **Mechanik (hart, ohne zweite RLS-Achse):** Jede `payment` läuft gegen genau ein `bank_account` (`bank_account_id NOT NULL`). Objekt/Mandat der Zahlung **folgen aus dem Konto** und sind per **Composite-FK** `payment(organization_id, bank_account_id, property_id) → bank_account(organization_id, id, property_id)` an das Konto gebunden. „Zahlung auf Konto X behauptet Objekt Y, obwohl Konto X zu Objekt Z gehört" ist damit **DB-unmöglich** — nicht nur Konvention. Voraussetzung: UNIQUE-Index `(organization_id, id, property_id)` auf `bank_account` (§1.1, §8).
- **Warum nicht die volle zweite RLS-Achse (Option C):** Sie wäre die stärkste Isolation, ist aber für ein MVP überschießend und stünde den **mandatsübergreifenden Verwaltungs-Sichten** (Fristenmonitor über alle WEGs, offene Posten gesamt) im Weg, die ADR-0001 als gewollten Vorteil der Shared-DB nennt. Die Composite-FK-Kohärenz sichert das konkret gefährdete Szenario (Querbuchen von Geld) **hart** und lässt die spätere C-Aufrüstung offen.
- **Konto-Typ-↔-Zahlungstyp-Stimmigkeit (Plausibilität):** Kaution aufs Kautionskonto, Rücklagenanteil aufs Rücklagenkonto — im MVP als **Warnung/Plausibilität**, nicht als harter CHECK (real-world-Daten sind unsauber). **[§18/fachlich F6-2]**, fachlich abzunehmen. Die Kohärenz Objekt↔Konto bleibt **hart**.

---

## 5. GoBD — Zahlungen unveränderbar (Storno statt Edit) [ENTSCHEIDUNG]

> **[ENTSCHEIDUNG E6-2]: Eine verbuchte Zahlung ist ein unveränderbarer Buchungsbeleg; Korrektur = Storno + Neubuchung.** *(Empfehlung; vollständige Begründung in ADR-0009.)*

Zahlungen sind **Buchungsbelege** (GoBD, SDD §6.6/§17, 8-Jahres-Aufbewahrung). M6 wendet das **bestehende ADR-0003-Muster** an — **nichts wird neu erfunden**:

- **`draft` → `booked`-Zweiteilung** (analog `cost_entry.draft→confirmed`): Im Status `draft` ist die Zahlung frei editier-/löschbar (reine Erfassungs-Korrektur, kein GoBD-Vorgang). Mit dem bewussten **Verbuchen** (`booked`) wird sie eingefroren. **[ENTSCHEIDUNG E6-3]** ob die Zweiteilung geführt wird (empfohlen) oder eine Zahlung sofort `booked` ist — Empfehlung: Zweiteilung, weil sie das etablierte Muster spiegelt und Tipp-Korrekturen nicht zu Storni zwingt.
- **Status-bewusster `BEFORE UPDATE/DELETE`-Trigger** (Einfrier-Bedingung `status='booked'`, Muster `0011`/`trg_settlement_run_immutability`): Inhaltstragende Spalten (`amount`, `direction`, `value_date`, `bank_account_id`, `property_id`, `management_mandate_id`, `tenant_profile_id`, `owner_profile_id`, `payment_type`, `lease_agreement_id`, `period_year`, `period_month`, `settlement_run_id`, `status`) sind nach `booked` per `IS DISTINCT FROM` gesperrt; **einzig erlaubte** Mutation einer `booked`-Zeile: das Setzen von `reversed_by_id` (Storno-Kette) + `updated_at`. DELETE einer `booked`-Zeile → immer Exception. `draft`-Zeilen sind frei (Trigger lässt sie durch). Volles CRUD-Grant + Trigger als alleinige statusabhängige Sperre (E3-5-Linie wie `cost_entry`/`settlement_*`, weil ein editierbarer `draft`-Zustand existiert).
- **Korrektur = Storno + Neubuchung:** Storno = neue `payment`-Zeile mit gegenläufiger `direction`, `reverses_id` → Original; das Original wird über `reversed_by_id` darauf verwiesen und bleibt unverändert (GoBD, SDD §14). Die korrekte Buchung ist eine weitere neue Zeile. Im Service als geführter `reverse_payment(...)`-Vorgang (analog `create_correction_run`, settlement_service).
- **Verzahnung mit finalisierten Abrechnungen (der eigentliche Grund):** Eine finalisierte Abrechnung friert ihren `advance_payments_total`/Ist als **Kopie** in den Snapshot (M3 §1.2, M4 §1.5). Ein späterer Storno verändert die **finalisierte** Abrechnung **nicht** (Snapshot bleibt unveränderbar); er wirkt erst auf eine neue (Storno-/Korrektur-)Abrechnung. So bleibt die Kette konsistent: unveränderbare Zahlung → unveränderbarer Abrechnungs-Snapshot → Korrektur stets als neue Version.
- **Audit-Trail:** `payment.created`, `payment.booked`, `payment.reversed` über das bestehende `AuditRepository` (append-only `audit_event`, M0).
- **Aufbewahrung/DSGVO:** `payment.retention_until` ist der Aufbewahrungsanker (8 Jahre, architecture.md §3.3); eine Zahlung als Buchungsbeleg fällt unter die gesetzliche Aufbewahrung — eine DSGVO-Löschanfrage führt (wie bei Belegen) zu **Sperrung/Verarbeitungseinschränkung**, nicht Hard-Delete, solange `retention_until` läuft (M9-Mechanik, hier nur der Anker).

---

## 6. Abrechnungs-Anbindung (Kern) — welche Zahlung → welche Abrechnungsgröße

Dies ist die korrektheitskritischste Stelle von M6 und der Grund, warum `engine-correctness` es designt. Die Brücke `advance_payments_source: MANUAL → DERIVED` ist in M3/M4 vorbereitet; M6 definiert **präzise**, welche Zahlungen in welche Abrechnungsgröße fließen — **ohne Doppelzählung, klar abgegrenzt, manuell als Fallback**.

### 6.1 Grundprinzip: eine reine Ableitungsfunktion, kein Matching

```
derive_advance_total(payments, filter) -> Decimal
   = Σ amount  über alle payments mit:
       status = 'booked'  AND  reversed_by_id IS NULL  (nicht storniert)
       AND payment_type ∈ filter.types
       AND period_year  = filter.year
       AND (filter.lease_agreement_id IS NULL OR lease_agreement_id = filter.lease_agreement_id)
       AND (filter.owner_profile_id  IS NULL OR owner_profile_id  = filter.owner_profile_id)
```

- **Reine Funktion** (`app/engine/mietkonto.py`), `Decimal` ein/aus, keine DB — testbar, deterministisch. Der Service lädt die passenden `payment`-Zeilen (Repository-Query mit genau diesem Filter) und übergibt sie der Funktion; die Funktion summiert. **Kein Bank-Matching, kein Parsing** (SDD §3).
- **Keine Doppelzählung — garantiert durch drei Eigenschaften:** (1) jede Zahlung hat **genau einen** `payment_type` und **genau einen** Periodenbezug (`period_year`) und Adressaten; (2) **stornierte** Zahlungen (`reversed_by_id IS NOT NULL`) und Storno-Zeilen werden konsistent behandelt (entweder beide raus, oder beide rein — netto null; siehe §6.6); (3) die Abrechnungs-Filter sind **disjunkt** je Größe (NK-Vorauszahlung ≠ Hausgeld-Vorschuss ≠ Kaltmiete). Eine Zahlung kann damit in **höchstens eine** Abrechnungsgröße fließen.

### 6.2 BK-Abrechnung (M3, `advance_payments_total`) ← `NK_VORAUSZAHLUNG`

**Präzise Zuordnung:**

| Abrechnungsgröße | M3-Feld | gespeiste Zahlungen (Typ + Periode + Adressat) |
| --- | --- | --- |
| Vorauszahlungssumme des Mieters | `settlement_run.advance_payments_total` (`source='DERIVED'`) | `payment` mit `payment_type='NK_VORAUSZAHLUNG'` **AND** `period_year` = Abrechnungsjahr des Laufs **AND** `lease_agreement_id` = `settlement_run.lease_agreement_id` **AND** `status='booked'` **AND** nicht storniert. |

- **Nur die NK-Vorauszahlung** fließt in die BK-Abrechnung — **nicht** die Kaltmiete (`KALTMIETE` ist Mietkonto-Soll-Ist, keine Betriebskosten). Das ist die schärfste Abgrenzung: ein Mieter zahlt oft Kaltmiete + NK in einem Betrag, aber im System sind es **zwei getrennte `payment`-Zeilen mit zwei Typen** (oder eine Aufteilung beim Erfassen). Die BK-Abrechnung zieht **ausschließlich** den `NK_VORAUSZAHLUNG`-Anteil.
- **Adressatengenau:** Bei unterjährigem Mieterwechsel hat jeder Mieter seinen **eigenen** `lease_agreement_id`; die DERIVED-Summe je Lauf zieht nur die Zahlungen **dieses** Leases (konsistent zu M3 §3.5: ein `settlement_run` je Lease).
- **Periodengenau:** `period_year` der Zahlung muss zum Abrechnungsjahr passen. Vorauszahlungen für 2025 fließen in die 2025-BK, nicht in 2024 — analog zur `service_period`-Logik der Kosten (M3 §3.4). **[§18/fachlich F6-7]** Behandlung von Zahlungen am Jahresübergang (Dezember-Miete im Januar gezahlt / Vorauszahlung für das Folgejahr): provisorisch maßgeblich ist der **erfasste `period_year`** (manuell zugeordnet), nicht das `value_date`. Fachlich abzunehmen.

### 6.3 WEG-Abrechnung (M4, Zahlungsstand) ← `HAUSGELD_VORSCHUSS` (nachrichtlich, NICHT in die Spitze)

**Präzise Zuordnung — und die wichtigste Abgrenzung von M6:**

| Abrechnungsgröße | M4-Träger | gespeiste Zahlungen | fließt in die Abrechnungsspitze? |
| --- | --- | --- | --- |
| **Soll-Vorschuss** des Eigentümers | `OwnerAdvanceInput.plan_advance_total` (aus `economic_plan`, §2.2) | **keine Zahlungen** — kommt aus dem **finalisierten Plan** | **JA** — der Subtrahend der Spitze (ADR-0007). |
| **Ist-Zahlung** (Zahlungsstand) des Eigentümers | `WegComputeExtras.paid_advances` (heute MANUAL) → **DERIVED** | `payment` mit `payment_type='HAUSGELD_VORSCHUSS'` **AND** `period_year` = Abrechnungsjahr **AND** `owner_profile_id` = Eigentümer **AND** `status='booked'` **AND** nicht storniert | **NEIN** — **nur nachrichtlich** (Zahlungsstand/Rückstand). |

- **Die Abrechnungsspitze bleibt `Ist-Kosten − Soll-Vorschuss(Plan)` (ADR-0007 E4-3a) — Zahlungen fließen NICHT in die Spitze.** Das ist die WEMoG-Systematik: Beschlussgegenstand ist die Anpassung gegen das **Soll**, nicht gegen das tatsächlich Gezahlte. Ein säumiger Eigentümer hat dieselbe Spitze wie ein zahlender (gleiches Soll, gleiche Ist-Kosten); sein **Rückstand** ist eine getrennte, nachrichtliche Größe (Mahnsache, M7). M6 ändert daran **nichts** — es ersetzt nur die **MANUAL** erfassten `paid_advances` (der heutige nachrichtliche Zahlungsstand) durch die **DERIVED**-Summe aus Zahlungen.
- **Konsequenz:** M6 berührt die korrektheitskritische Spitze **gar nicht**. Es füllt ausschließlich den **nachrichtlichen** Zahlungsstand-Ausweis (`settlement_line(line_type='ADVANCE_PAYMENT')` optional, M4 §2.2/§2.4). Die in M4 etablierte Trennung (Spitze beschlussrelevant ↔ Zahlungsstand nachrichtlich) wird damit **gestärkt**, nicht verwässert.

### 6.4 Rücklagenanteil ← `RUECKLAGENANTEIL` (nachrichtlich, nicht in die Spitze)

`RUECKLAGENANTEIL`-Zahlungen speisen den **Ist-Stand der Rücklagenzuführung** je Eigentümer (nachrichtlich). Sie fließen **nicht** in die Abrechnungsspitze (die Zuführung ist Vermögensbildung, keine Ist-Kosten — M4 §3). Der **Soll**-Zuführungsanteil kommt aus dem Plan/den `ERHALTUNGSRUECKLAGE`-Kostenpositionen (M4 §3.3); die DERIVED-Zahlungssumme ist der **Ist**-Abgleich. Optionaler Beitrag zum Vermögensbericht (§6.5).

### 6.5 Vermögensbericht (M4, §28 IV) ← optional Kontostand DERIVED

M4 nutzt heute manuell erfasste Stichtagssalden (ADR-0008). Ab M6 **kann** der Vermögensbericht den Kontostand je `bank_account` **aus `payment` ableiten** (`Σ IN − Σ OUT` bis Stichtag, + optional `opening_balance`, §1.1) statt manuell. **[ENTSCHEIDUNG E6-7]** ob M6 den DERIVED-Kontostand für den Vermögensbericht anbietet (empfohlen: ja, als Option neben dem manuellen Stichtagssaldo) oder M4s manuellen Weg unverändert lässt. Empfehlung: DERIVED **anbieten**, MANUAL als Fallback — exakt das `source`-Muster. Forderungen/Verbindlichkeiten (M4 §6.4) bleiben manuell (M6 modelliert keine Lieferanten-Zahlungsstati über das Nötige hinaus).

### 6.6 Storno-Behandlung in der Ableitung (keine Doppel-/Nullzählung)

Die DERIVED-Summe muss bei Storni **netto** korrekt sein:
- **Variante A (empfohlen):** Ableitung zählt nur Zahlungen mit `reversed_by_id IS NULL` (nicht stornierte Originale) **und** schließt Storno-Zeilen (`reverses_id IS NOT NULL`) aus. Eine stornierte Zahlung + ihr Storno fallen damit beide raus → netto null. Klar und einfach.
- **Variante B:** alle `booked` zählen, Storni mit umgekehrtem Vorzeichen — netto ebenfalls null, aber fehleranfälliger (Vorzeichen-Logik). **Empfehlung: Variante A.** Golden-getestet (§7): „stornierte Zahlung fließt nicht in die DERIVED-Summe".

### 6.7 Manuell bleibt als Fallback (das `source`-Diskriminator-Muster)

- `advance_payments_source='MANUAL'` (M3/M4-Default) bleibt vollständig funktionsfähig: der Sachbearbeiter setzt die Vorauszahlungssumme von Hand (z. B. wenn Zahlungen nicht im System erfasst sind). **M6 erzwingt DERIVED nicht.**
- `advance_payments_source='DERIVED'` wird beim Anlegen/Berechnen eines Laufs **optional** gewählt: dann zieht der Service die Summe per §6.1-Funktion und setzt `advance_payments_total` aus den Zahlungen. Beim **Finalisieren** wird der so ermittelte Wert (wie jeder Wert) als **Kopie** in den Snapshot eingefroren — ab da ist er unveränderlich, auch wenn später Zahlungen storniert werden (§5, ADR-0009 #3).
- **Übergang ohne Schemabruch:** Die Spalten existieren bereits und sind im Immutability-Trigger gelistet (`0011`). M6 ändert **kein** Abrechnungs-Schema; es füllt nur den `DERIVED`-Pfad im Service.

> **[ENTSCHEIDUNG E6-8] Zeitpunkt der Ableitung.** DERIVED-Wert **bei jedem `compute`** frisch ziehen (immer aktuell, bis `finalize` einfriert) — empfohlen — vs. einmal beim Anlegen fixieren. Empfehlung: bei jedem `compute` frisch (der `draft` spiegelt den aktuellen Zahlungsstand); `finalize` friert den dann gültigen Wert ein. Bestätigen.

---

### 6.8 Bankumsätze und Dateiimport (Phase 2, T15–T19)

Importierte `bank_transaction`-Zeilen sind zunächst **NEU**, keine gebuchten
`payment`-Zeilen und deshalb noch kein Ist im Miet-/Eigentümerkonto. Sie tragen
signed EUR-Decimal-Beträge; RLS und Composite-FKs sichern Organisation und Konto.
Ein späterer Zahlungslink muss zum gleichen Konto gehören und ist nach Buchen
unveränderlich. Bestehende Zahlungen und finalisierte Abrechnungen werden beim
Import nicht verändert.

`POST /api/bank-accounts/{id}/imports` nimmt eine Datei (`file`, CSV/XML/ZIP).
`dry_run=true` ist die sichere Vorgabe: Anzahl, Gesamtsumme, neue Summe,
Buchungszeitraum, Dubletten und maximal 100 Vorschauzeilen, **keine Persistenz**.
Mit `dry_run=false` erfolgt die Übernahme atomar unter Kontosperre und zusätzlichem
Unique-Conflict-Schutz; derselbe Auszug erzeugt erneut null Zeilen, auch bei
konkurrierenden Importen. Fremde Konten liefern 404; READ_ONLY darf nicht importieren.
Bekannte Quell-IBANs müssen mit einer hinterlegten Ziel-IBAN übereinstimmen.
Parser-/Formatfehler liefern 422, übergroße Uploads 413, jeweils ohne Teilübernahme.
Formate, Limits und Grenzen bei fehlenden bankseitigen Transaktions-IDs: README.
Die Oberfläche `/bankkonten/{id}/umsaetze` bietet Statusfilter, Pagination und
für Schreibrollen Datei → Vorschau → Übernehmen. Ein Dateiwechsel verwirft die
alte Freigabe; Rückmeldungen nennen auch null neue Umsätze. Der echte lokale
T19-Browserlauf prüft CSV, Reload, wiederholten Import sowie dieselben Buchungen
als CAMT-XML/ZIP ohne Duplikate. Kontobezogene Liste über
`GET /api/bank-accounts/{id}/transactions`, paginiert (50, maximal 100 Zeilen).
API-Zuordnung und gebuchte Zahlungen bleiben T21–T22; diese sind noch nicht freigegeben.

### 6.9 Zuordnungsvorschläge (Phase 2, T20)

`suggest_matches` arbeitet rein auf einem Bankumsatz und bereits aggregierten
`OpenItem`-Werten aus `account_statement` (Soll aus Mietvertrag/Wirtschaftsplan,
abzüglich gebuchtem Ist). Sie lädt keine Daten und schreibt keine Zahlungen.
Der spätere Service muss die Ziele auf Organisation, Konto und Objekt begrenzen.
Bekannte abweichende IBAN verhindert einen Treffer; Namen werden nur anhand
normalisierter vollständiger Wörter verglichen, nicht als unscharfe Teilzeichenfolge.
Ein expliziter Monat im Zweck ist maßgeblich. Der Wertstellungsmonat ist nur ein
prüfpflichtiger Fallback; mehrere widersprüchliche Monate werden nicht geraten.

Warmmiete 850 EUR wird bei 700 EUR offener Kaltmiete und 150 EUR offenen NK exakt
aufgeteilt. Bereits bezahlte Komponenten werden nicht nochmals vorgeschlagen.
Hausgeld nutzt das vorhandene Eigentümer-Soll. Teilzahlung auf eine eindeutige
Komponente ist centgenau; bei unklarer Warmmiet-Aufteilung bleiben die Splitbeträge
`None`, `unallocated_amount` enthält den vollen Eingang und `requires_review` ist
wahr. **Keine erfundene Tilgungsreihenfolge und keine automatisch buchbare
Aufteilung** für diesen Fall. Mehrere passende Ziele, abweichender Name oder
unbekannter Monat senken die Konfidenz bzw. verlangen Prüfung. Kaution, Storno,
Rückbuchung, gemischter Miet-/Hausgeldzweck, unbekannte Identität und Überzahlung
werden nicht als sichere reguläre Soll-Zuordnung umgedeutet; manuell bleiben sie
bearbeitbar. API-/UI-Integration und erneute Validierung sind erst T21/T22.

## 7. Synthetische Golden-Tests (objektive M6-Messlatte)

Es gibt **keine** realen Beispieldaten. Prüffälle sind **synthetisch** aus den Regeln + **handberechneten** Soll-/Ist-/Saldo-Werten (jeder Cent), exakt wie M3/M4 §9. Beide Test-Arten verbindlich für die M6-Abnahme; sie laufen gegen die **reine Engine** (höchste Testdichte) und gegen Service/DB.

### 7.1 Golden-Tests (handberechnet)

1. **Soll-Ist je Mietvertrag, voll bezahlt:** Lease, Kaltmiete 700,00 € + NK-Vorauszahlung 150,00 € = 850,00 €/Monat, 12 Monate, 12 Zahlungen à 850,00 €. Soll: Jahres-Soll 10.200,00 €, Ist 10.200,00 €, **Saldo 0**; offene Posten leer. Prüft Soll-Generierung (§2) + getrennte Komponenten.
2. **Teilzahlung → offener Posten:** ein Monat 850,00 € Soll, Zahlung 600,00 €. Soll: offener Posten 250,00 € (offen > 0). Prüft §3.2.
3. **Überzahlung → Guthaben:** ein Monat 850,00 € Soll, Zahlung 1000,00 €. Soll: offener Posten −150,00 € (Guthaben). Prüft §3.3.
4. **Unterjähriger Mietbeginn (tagegenauer Anschnitt):** Lease ab 15.03., NK-Vorauszahlung 150,00 €/Monat; März anteilig (17/31 Tage). Soll: März-NK-Soll = `distribute`-genauer Anteil; ganze Monate ab April voll. Prüft §2.1 + Lese-Grenze (ADR-0006).
5. **DERIVED-NK-Vorauszahlung in einer BK-Abrechnung == MANUAL (Exit-Kern):** Mieter zahlt monatlich 700,00 € `KALTMIETE` + 150,00 € `NK_VORAUSZAHLUNG` (12×), `period_year=2025`, ein Lease. (a) BK-Abrechnung mit `advance_payments_source='MANUAL'`, `advance_payments_total=1800,00` (12×150). (b) BK-Abrechnung mit `advance_payments_source='DERIVED'`. Soll: **beide ergeben exakt `advance_payments_total=1800,00`** und **identische** `balance`; die `KALTMIETE`-Zahlungen (8400,00 €) fließen in **keine** der beiden. **Dies ist das direkte Abbild des M6-Exit-Kriteriums** („eine Abrechnung zieht erfasste Zahlungen automatisch") und der schärfste Abgrenzungstest (Kaltmiete ≠ NK).
6. **Hausgeld-Soll-Ist + Spitze unberührt:** WEG, Eigentümer A: Plan-Soll-Vorschuss 5500,00 € (aus finalisiertem Plan, §2.2), Ist-Kosten-Anteil 6000,00 €, gezahlte `HAUSGELD_VORSCHUSS` = 5000,00 € (Rückstand 500,00 € ggü. Soll). Soll: **Abrechnungsspitze = 6000 − 5500 = +500,00 € (unverändert ggü. M4 Golden-Test 2)**; der Zahlungsstand (Ist 5000,00 €, Rückstand zum Soll 500,00 €) erscheint **nachrichtlich**, fließt **NICHT** in die Spitze. Prüft §6.3 — die Kern-Abgrenzung „Zahlungen nicht in die Spitze".
7. **Storno fließt nicht in DERIVED:** Mieter zahlt NK 150,00 €, dann Storno (gegenläufig), dann korrekte 160,00 €. Soll: DERIVED-NK-Summe = 160,00 € (Original + Storno netto null). Prüft §6.6 + GoBD-Storno (§5).
8. **Vermögenstrennung — Querbuchen scheitert (DB-Negativtest):** Versuch, eine `payment` mit `bank_account` der WEG A, aber `property_id` der WEG B (gleiche Org) zu schreiben → vom Composite-FK **abgewiesen**. Prüft §4 / ADR-0010.
9. **GoBD — `booked`-Zahlung unveränderbar (DB-Negativtest):** UPDATE von `amount`/Zuordnung einer `booked`-Zahlung → Trigger-Exception; DELETE → Exception; nur `reversed_by_id` setzbar. Prüft §5 / ADR-0003-Muster.

Jeder Golden-Test prüft **nicht nur die Endsumme**, sondern die **einzelnen offenen Posten, Salden und (bei §6) die exakte DERIVED-Summe** gegen die Handrechnung (sonst rutscht ein kompensierender Doppelfehler durch — M3/M4-Disziplin).

### 7.2 Property-based Invarianten (Hypothesis, über zufällige Eingaben)

- **I6-A „Saldo == Σ Soll − Σ Ist":** für zufällige Soll-/Zahlungslisten gilt der berechnete Saldo exakt (Decimal, kein Float-Drift).
- **I6-B „DERIVED-Summe == Σ passender, nicht-stornierter Zahlungen":** für zufällige Zahlungsmengen ist die Ableitung (§6.1) exakt die Summe der typ-/perioden-/adressatengleichen `booked`-nicht-stornierten Zahlungen; Storni netto null.
- **I6-C „keine Doppelzählung / Disjunktheit":** keine einzelne Zahlung fließt in mehr als eine Abrechnungsgröße (NK ⊥ Hausgeld ⊥ Kaltmiete ⊥ Rücklage).
- **I6-D „Spitze unabhängig von Zahlungen":** für zufällige Ist-Kosten + Plan-Soll + beliebige `HAUSGELD_VORSCHUSS`-Zahlungen ist die WEG-Abrechnungsspitze **invariant** gegenüber den Zahlungen (== `Ist − Soll(Plan)`, ADR-0007). **Die zentrale M6-Sicherheits-Invariante.**
- **I6-E „DERIVED == MANUAL bei gleichem Wert":** setzt man `advance_payments_total` (MANUAL) = DERIVED-Summe, ist das Abrechnungsergebnis bit-identisch (gleicher Snapshot-Hash). Prüft die Brücke.
- **I6-F „Vermögens-Kohärenz":** keine `booked`-Zahlung hat ein `property_id`, das vom `property_id` ihres `bank_account` abweicht (Composite-FK-Invariante).

### 7.3 Der Exit-Test (DERIVED == MANUAL)

Golden-Test 5 + Invariante I6-E sind zusammen der objektive Nachweis des M6-Exit: eine BK-Abrechnung, die ihre Vorauszahlung aus erfassten `NK_VORAUSZAHLUNG`-Zahlungen **ableitet**, ergibt **exakt dasselbe** (bis auf den Cent, bis auf den Snapshot-Hash) wie die manuelle Variante mit derselben Summe. Damit ist „eine Abrechnung zieht erfasste Zahlungen automatisch" **nachrechenbar** belegt — ohne die geprüfte M3-Engine anzutasten.

---

## 8. Implementierungs-Checkliste für `bulk-impl` (Roadmap M6 → Opus 4.6)

> M6 ist laut Roadmap-Modellzuordnung **`bulk-impl` (Opus 4.6)** — Geldfluss-Erfassung ist überwiegend Bulk-Engineering. **Aber:** die **Abrechnungs-Anbindung (§6) ist korrektheitskritisch** und als **reine, getestete Ableitungsfunktion** zu bauen; bei Unklarheit in der DERIVED-Logik, der Vermögens-Kohärenz oder der GoBD-Mechanik **an `engine-correctness` eskalieren** (gemäß `bulk-impl.md`-Eskalationsregel). Reihenfolge wie M3/M4: **Engine zuerst (reine Funktionen + Tests), dann Persistenz/Trigger, dann Service, dann API, zuletzt PDF/Anzeige.** **Voraussetzung:** der Mensch hat die [ENTSCHEIDUNG]en (§10) und ADR-0009/0010 bestätigt. Vorher kein Commit. **Alle 291 bestehenden Tests bleiben grün** (additiv).

### 8.1 Engine als reine Funktionen (zuerst, höchste Testdichte)
- [ ] `app/engine/mietkonto.py` (neu, **bewusst getrennt** von `services`/`api`/`models` — SDD §16): keine DB-/HTTP-/ORM-Importe; `Decimal`/`date`/Dataclasses ein/aus.
  - [ ] `tenant_expected_charges(...)`, `owner_expected_charges(...)` — Soll-Generierung, monatlicher Maßstab, tagegenaue Anschnitte (§2). **Wiederverwenden:** `app/engine/periods.py` (tagegenau), `app/engine/rounding.py::distribute_amount` (Anschnitt-/Ratenrundung, ADR-0005). **Nicht duplizieren.**
  - [ ] `account_statement(...)` — Soll/Ist/Saldo/offene Posten (§3), reine Decimal-Arithmetik.
  - [ ] `derive_advance_total(payments, filter) -> Decimal` — die DERIVED-Ableitung (§6.1), inkl. Storno-Behandlung Variante A (§6.6).
- [ ] Golden-Tests (§7.1) `tests/engine/test_golden_mietkonto.py` (handberechnet); Property-based (§7.2) `tests/engine/test_invariants_mietkonto.py` (Hypothesis), insb. I6-D (Spitze unabhängig von Zahlungen) und I6-E (DERIVED==MANUAL).

### 8.2 Persistenz & Trigger (danach)
- [ ] Modell `app/models/payment.py` (Stil `cost_entry.py`/`settlement_run.py`: Mixins, `Numeric`, `Mapped`, String+CHECK, FK `RESTRICT` zu `organization`). Geld `Decimal`. `reverses_id`/`reversed_by_id` (Storno-Kette).
- [ ] **Migration `0016_payment_table`:** `payment` mit allen CHECK-Constraints (§1.2/§1.4), FKs, **Composite-FK** `(organization_id, bank_account_id, property_id) → bank_account(organization_id, id, property_id)` (ADR-0010) — **erfordert vorab** UNIQUE-Index `(organization_id, id, property_id)` auf `bank_account` (additiv, in dieser Migration). Indizes (§1.2). Optional: `bank_account.opening_balance`/`opening_balance_date` (E6-6, falls bestätigt). `downgrade()` setzt alles zurück.
- [ ] **Migration `0017_payment_rls_policies`:** vier Policies für `payment` exakt im Muster `0010`/`0014` (`NULLIF(current_setting('app.current_org_id', true),'')::uuid`, fail-closed). Volles CRUD-Grant (editierbarer `draft` → Trigger-only-Freeze, E3-5-Linie).
- [ ] **Migration `0018_payment_immutability_trigger`** (ADR-0003-Muster von `0011`): `BEFORE UPDATE/DELETE`-Trigger für `payment` (Einfrier-Bedingung `status='booked'`; inhaltstragende Spalten per `IS DISTINCT FROM` gesperrt; einzig erlaubte Mutation `reversed_by_id` + `updated_at`; DELETE einer `booked`-Zeile → Exception; `draft` frei). Trigger-Quelltext ausführlich kommentiert; `downgrade()` droppt Trigger + Funktion.
- [ ] `TENANT_TABLES` in `tests/test_rls_coverage.py` um `payment` erweitern (eine Zeile).
- [ ] Repository `app/repositories/payment_repo.py` (Stil `cost_entry_repo.py`): Aktiv-/Booked-Query (`status='booked' AND reversed_by_id IS NULL`); die **DERIVED-Filter-Queries** (§6.1) je Größe (NK je Lease/Jahr; Hausgeld je Owner/Jahr; Kontostand je Konto bis Stichtag).

### 8.3 Service (Erfassung, Verbuchen, Storno — atomar)
- [ ] `app/services/payment_service.py` (Stil `cost_entry_service.py`): `create_draft`, `update_draft`, `book` (`draft→booked`, atomar, Audit), `reverse_payment` (Storno = neue `booked`-Zeile + `reversed_by_id` setzen — die **einzige** erlaubte Mutation der `booked`-Originalzeile, analog `create_correction_run`), `account_statement_for(person/lease)` (lädt Soll-Eingaben + Zahlungen, ruft die reine Engine), `open_items_for(...)`.
- [ ] **Abrechnungs-Anbindung (reine, getestete Ableitung):**
  - [ ] `settlement_service.py` (M3): einen `DERIVED`-Pfad ergänzen — beim `compute`/`finalize` eines Laufs mit `advance_payments_source='DERIVED'` die NK-Summe per `payment_repo` + `derive_advance_total` (§6.2) ziehen, `advance_payments_total` setzen. **MANUAL-Pfad unverändert.** Die M3-Engine selbst (`compute_bk_settlement`) bleibt **unangetastet** — sie bekommt nur denselben `advance_payments_total` wie bisher, jetzt aus Zahlungen.
  - [ ] `weg_settlement_service.py` (M4): `WegComputeExtras.paid_advances` optional aus Zahlungen ableiten (`HAUSGELD_VORSCHUSS` je Owner/Jahr, §6.3) statt manuell. **Die Spitze (`Ist−Soll(Plan)`) wird NICHT berührt** — nur der nachrichtliche `paid_advances`-Ausweis. Engine (`compute_weg_jahresabrechnung`) unverändert.
  - [ ] optional `asset_report` (M4): DERIVED-Kontostand je `bank_account` anbieten (§6.5, E6-7), MANUAL als Fallback.
- [ ] `audit_event`-Actions: `payment.created`, `payment.booked`, `payment.reversed`.

### 8.4 API (API-first, RBAC wie M0–M4)
- [ ] `app/api/payments.py`: `POST` (Zahlung anlegen, `require_write`), `PATCH /{id}` (nur `draft`), `POST /{id}/book`, `POST /{id}/reverse`, `GET` (Liste/Detail, alle Rollen lesen, Filter nach Konto/Person/Periode/Typ), `DELETE /{id}` (nur `draft`). READ_ONLY: nur GET. Fremde ID → **404** (wie M0–M4).
- [ ] `app/api/mietkonto.py` (oder in `payments.py`): `GET /accounts/{lease|owner}/statement` (berechnetes Mietkonto/Soll-Ist), `GET /open-items` (berechnete offene Posten je Person/Objekt). Read-only, alle Rollen.
- [ ] Router in `app/main.py` registrieren.

### 8.5 Anzeige/PDF (zuletzt, strikt getrennt)
- [ ] Keine **neuen** Abrechnungs-PDFs in M6 (die BK-/WEG-PDFs aus M3/M4 zeigen den Vorauszahlungsabzug bereits; sie rendern den nun ggf. DERIVED-Wert unverändert — **PDF rechnet nichts neu**, Test: PDF-Werte == `settlement_line`-Werte bleibt grün). Optional: eine einfache **Mietkonto-/Offene-Posten-Ansicht** (Liste, read-only) als Vorbereitung für M7-Mahnschreiben.

### 8.6 Definition of Done für M6 (Übergabe an M7)
- [ ] Engine-Paket rein (keine DB/HTTP-Importe); `distribute_amount`/`periods` **wiederverwendet**, nicht dupliziert; `Decimal` durchgängig.
- [ ] **Golden-Tests §7.1 grün** (handberechnet), insb. Test 5 (DERIVED-NK == MANUAL, Kaltmiete fließt nicht), Test 6 (Spitze unberührt), Test 7 (Storno netto null).
- [ ] **Property-based Invarianten §7.2 grün** (I6-A bis I6-F), insb. **I6-D (Spitze unabhängig von Zahlungen)** und **I6-E (DERIVED==MANUAL)**.
- [ ] `payment` mit RLS (vier Policies, fail-closed) + ADR-0003-Trigger (`booked` unveränderbar) + Composite-FK-Vermögens-Kohärenz (ADR-0010).
- [ ] `book`/`reverse` atomar; `audit_event` für alle Vorgänge; Korrektur = Storno + Neubuchung (nie Mutation).
- [ ] DERIVED-Pfad in M3- **und** M4-Service als **reine Ableitung**; **M3/M4-Engine unverändert**; MANUAL bleibt Fallback. Keine Doppelzählung (Disjunktheit der Filter belegt, I6-C).
- [ ] DB-Negativtests grün: Querbuchen scheitert (§7.1 #8), `booked`-Zahlung unveränderbar (§7.1 #9).
- [ ] RLS-Coverage-Test + `test_tenant_isolation` um `payment` erweitert; **alle 291 bestehenden Tests bleiben grün** (M3/M4-Abrechnung unverändert; insb. der Snapshot-Hash bestehender Golden-Tests darf sich nicht ändern).
- [ ] M6-Exit objektiv erfüllt: Soll-Ist je Person stimmt (Golden 1–4); eine BK-Abrechnung zieht erfasste NK-Vorauszahlungen automatisch und ergibt **dasselbe** wie manuell (Golden 5 / I6-E).
- [ ] Keine **[ENTSCHEIDUNG]**/**[§18/fachlich]** unmarkiert „weggeraten"; provisorische Annahmen klar gekennzeichnet; fachliche Abnahme (StB/Fachanwalt) empfohlen.

---

## 9. Getroffene Annahmen (M6)

| # | Annahme | Auswirkung wenn falsch | Klären mit |
| --- | --- | --- | --- |
| **M6-A1** | Soll **abgeleitet** (nicht persistiert), monatlicher Maßstab (ADR-0009/0007). | Persistiertes Soll bräuchte Materialisierungs-Job + zweite Wahrheit; Modell-/Service-Umbau. | Mensch (ADR-0009) |
| **M6-A2** | Zahlung **unveränderbar ab `booked`**; Korrektur = Storno + Neubuchung (ADR-0003-Muster). | Editierbare Zahlung verletzt GoBD + kann finalisierte Abrechnungen widersprüchlich machen. | Mensch/StB (ADR-0009) |
| **M6-A3** | Vermögenstrennung per **Composite-FK-Kohärenz**, keine zweite RLS-Achse (ADR-0010). | Volle mandatsgebundene DB-Isolation bräuchte zweite GUC/Policy-Achse. | Mensch (ADR-0010) |
| **M6-A4** | **NK-Vorauszahlung → BK** (DERIVED), **Kaltmiete NICHT**; **Hausgeld-Vorschuss → WEG nachrichtlich, NICHT in die Spitze** (Spitze bleibt `Ist−Soll(Plan)`, ADR-0007). | Falsche Anbindung verfälschte Abrechnungen / Spitze. | Mensch/StB/Fachanwalt (§6) |
| **M6-A5** | Zahlung trägt **expliziten** Periodenbezug (`period_year`); keine automatische Tilgungsreihenfolge (§ 366 BGB). | Automatische Verrechnung wäre eigener, fachlicher Baustein. | StB/Fachanwalt (§3.2/F6-6) |
| **M6-A6** | `bank_account` **ohne** Saldenspalte; Kontostand **abgeleitet** (optional + Eröffnungssaldo-Anker). | Gespeicherter Saldo driftet von Buchungen. | Mensch (§1.1) |
| **M6-A7** | Mahn-Persistenz (offene-Posten-Status) ist **M7**, nicht M6. | Vorgriff auf M7 vermieden; offene Posten in M6 nur berechnet. | (Roadmap M7) |
| **M6-A8** | Maßgeblich für Perioden-Zuordnung ist der **erfasste `period_year`**, nicht das `value_date` (Jahresübergang). | Andere Zuordnung am Jahresende. | StB (§6.2/F6-7) |
| **M6-A9** | Kaution nur **Bestandsführung**, keine Verzinsung im MVP. | Verzinsung/Anlageform wäre Zusatzbaustein. | StB/Fachanwalt (§3.4/F6-3) |

---

## 10. Zusammenfassung der offenen Entscheidungen (für den Menschen)

| # | Punkt | Typ | Empfehlung | Wann |
| --- | --- | --- | --- | --- |
| **E6-1** | `open_item` abgeleitet vs. persistiert | [ENTSCHEIDUNG] | **abgeleitet** (berechnet), keine Materialisierung (ADR-0009) | vor M6-Impl |
| **E6-2** | Zahlung unveränderbar (Storno) vs. editierbar | [ENTSCHEIDUNG] | **unveränderbar ab `booked`**, Storno + Neubuchung (ADR-0003-Muster, ADR-0009) | vor M6-Impl |
| **E6-3** | `draft`→`booked`-Zweiteilung vs. sofort `booked` | [ENTSCHEIDUNG] | **Zweiteilung** (spiegelt `cost_entry`; Tipp-Korrektur ≠ Storno) | bei Impl |
| **E6-4** | Vermögenstrennung: DB-Constraint jetzt vs. App-Layer | [ENTSCHEIDUNG] | **DB-Composite-FK-Kohärenz jetzt**, keine zweite RLS-Achse (ADR-0010; löst M0-A4) | vor M6-Impl |
| **E6-5** | `bank_account` ausbauen vs. unverändert | [ENTSCHEIDUNG] | **unverändert** (keine Saldenspalte; Saldo abgeleitet) + UNIQUE-Index für Composite-FK | vor M6-Impl |
| **E6-6** | Eröffnungssaldo-Anker an `bank_account` | [ENTSCHEIDUNG] | **ja** (klein, additiv: `opening_balance`/`_date`) | bei Impl |
| **E6-7** | Vermögensbericht-Kontostand DERIVED anbieten | [ENTSCHEIDUNG] | **ja**, DERIVED als Option, MANUAL Fallback (ADR-0008-Linie) | bei Impl |
| **E6-8** | Zeitpunkt der DERIVED-Ableitung | [ENTSCHEIDUNG] | **bei jedem `compute`** frisch; `finalize` friert ein | bei Impl |
| **F6-1** | Mahn-Persistenz (offene-Posten-Status) | [§18/fachlich] | **nicht M6** — Sache von M7 | M7 |
| **F6-2** | Konto-Typ ↔ Zahlungstyp: harter CHECK vs. Warnung | [§18/fachlich] | **Warnung** im MVP; Objekt↔Konto hart | vor produktiv |
| **F6-3** | Umfang Zahlungsarten (Kaution? weitere Typen?) | [§18/fachlich] | provisorisches Vokabular; `KAUTION` optional/abrechnungsneutral | vor produktiv |
| **F6-4** | Anschnittsmonat tagegenau vs. pauschal | [§18/fachlich] | provisorisch **tagegenau** (konsistent M3) | vor produktiv |
| **F6-5** | Eigentümer-Soll ohne finalisierten Plan | [§18/fachlich] | provisorisch: kein Soll ohne finalisierten Plan (analog M4 F4-1) | vor produktiv |
| **F6-6** | Tilgungsreihenfolge / § 366 BGB-Verrechnung | [§18/fachlich] | provisorisch: expliziter Periodenbezug, keine Auto-Verrechnung | vor produktiv |
| **F6-7** | Perioden-Zuordnung am Jahresübergang | [§18/fachlich] | provisorisch: erfasster `period_year` maßgeblich | vor produktiv |

> **Keine Selbst-Zertifizierung (Haltung):** Dieses Design macht Soll, Ist, offene Posten, die Vermögenstrennung und die Abrechnungs-Anbindung **transparent, deterministisch und nachrechenbar** (Golden-Tests + Property-based Invarianten als objektive Messlatte, insb. „Spitze unabhängig von Zahlungen" und „DERIVED == MANUAL"). Es kann **nicht** garantieren, dass das Mietkonto/Hausgeldkonto im Einzelfall buchhalterisch/prüfungsfest ist (M6 ist keine Finanzbuchhaltung). Die fachliche Richtigkeit (Mietkonto-Tiefe, Kaution, GoBD-Belegqualität manueller Zahlungen, Treuhand-/Fremdgeldtrennung, Verrechnungsregeln) ist vor produktivem Einsatz durch **Steuerberater/Fachanwalt** abzunehmen.

---

## 11. Was dieses Dokument bewusst NICHT entscheidet

- **Mahnwesen / Mahnstufen / `DunningNotice` / Mahn-Persistenz:** M7 (§0, ADR-0009 §1/F6-1) — M6 liefert nur die *berechneten* überfälligen Posten.
- **Bankimport / Zahlungsabgleich:** war Nicht-Ziel des ursprünglichen M6. Die verbindliche Phase 2 in `docs/mvp.md` erweitert dies um CSV/CAMT-Import (T15–T19) und geprüfte Zuordnung (T20–T22); fertiger Parser-/API-Stand siehe §6.8. MT940 bleibt nicht unterstützt.
- **Vollwertige Kautionsbuchhaltung** (Verzinsung, Anlageform, Rückzahlung bei Auszug): [§18/fachlich] (§3.4, F6-3).
- **Tilgungsreihenfolge / § 366 BGB-Verrechnung:** [§18/fachlich] (§3.2, F6-6).
- **Genaue Fälligkeitsregeln** (§ 556b BGB, Hausgeld-Fälligkeit): [§18/fachlich] (§2.1).
- **Zweite RLS-Achse (volle mandatsgebundene DB-Isolation):** bewusst nicht im MVP (ADR-0010 Option C) — Tür offen, nicht verbaut.
- **Endgültige Rechtsabnahme:** Steuerberater/Fachanwalt — nicht durch dieses Dokument ersetzbar.
