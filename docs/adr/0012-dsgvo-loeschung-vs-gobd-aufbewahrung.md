# ADR-0012: DSGVO-Löschung vs. GoBD-Aufbewahrung — Einschränkung statt Löschung, Anonymisierung nur ohne Aufbewahrungsbindung

Status: **Vorgeschlagen** — technische Mechanik in M9 implementiert (`engine/dsgvo.py`, `services/dsgvo_service.py`, `api/dsgvo.py`, Tests). Fachliche/rechtliche Abnahme durch DSB / Steuerberater:in / Fachanwalt:anwältin ausstehend (alle **[§18/fachlich]**-Punkte). Keine Selbst-Zertifizierung.
Datum: 2026-06-02 · Kontext: M9 · Bezug: [../architecture.md](../architecture.md) §3.3 · [../m2-gobd-belegfluss.md](../m2-gobd-belegfluss.md) §7 · [0003](0003-gobd-unveraenderbarkeit-mechanik.md) · SDD §15, §17, §18

> Diese ADR entscheidet die in `architecture.md` §3.3 und SDD §18 explizit offen gelassene Frage *„Detailtiefe des Lösch-/Anonymisierungskonzepts im Spannungsfeld zur revisionssicheren Aufbewahrung"* auf der **technischen** Ebene (welcher Mechanismus, welche Reconciliation-Invariante). Die **rechtliche** Hinlänglichkeit (welches Feld ist Belegbestandteil, welche Anonymisierungstiefe genügt, welches Startdatum/welche Dauer die Aufbewahrungsfrist) bleibt fachlich abzunehmen und ist hier durchgehend mit **[§18/fachlich]** markiert. **Keine Selbst-Zertifizierung.**

## Kontext

SDD §15/§17 fordern zwei Dinge, die in Spannung stehen:

1. **GoBD-/handelsrechtliche Aufbewahrung** (SDD §17, BEG IV ab 2025): Buchungsbelege, freigegebene Belege und finalisierte Abrechnungen sind **mindestens 8 Jahre revisionssicher und inhaltlich unverändert** aufzubewahren (Invariante I2, ADR-0003). Eine finalisierte Abrechnung enthält naturgemäß Namen/Anschrift des Adressaten.
2. **DSGVO-Löschanspruch / Auskunft** (Art. 15, 17, 18 DSGVO; SDD §15): Eine betroffene Person kann Löschung und Auskunft verlangen, „ohne die revisionssichere Belegaufbewahrung zu verletzen".

Der Schlüssel zur Auflösung steht bereits in `architecture.md` §3.3 und wird hier zur implementierbaren Regel verdichtet: **Trennung von Belegsubstanz und reinen Personenstammdaten** plus **Verarbeitungseinschränkung (Art. 18 DSGVO) statt Löschung**, solange eine gesetzliche Aufbewahrungspflicht als Rechtsgrundlage gegen den Löschanspruch besteht (Art. 17 Abs. 3 lit. b/e DSGVO).

Die Felder im Modell existieren seit M0/M1 (`person.deletion_requested_at`, `person.processing_restricted`, `person.anonymized_at`; `invoice_document.retention_until`, `payment.retention_until`). M9 liefert die **Mechanik** und die **Reconciliation-Invariante**, die entscheidet, *welcher* Person was passiert.

### Wichtige Eigenheit der Render-Zeit-Namensauflösung

Finalisierte Abrechnungs-/Mahn-PDFs lösen den Adressatennamen **zur Render-Zeit** aus `person`/Profil auf (siehe `settlement_pdf`/`dunning_pdf`: Name wird aus dem verknüpften `person`-Datensatz gezogen, nicht im PDF-Bytes eingefroren — die einzige eingefrorene Wahrheit ist der `snapshot_payload`/`included_items`). Würde man eine Person anonymisieren, die Adressat einer finalisierten Abrechnung ist, **renderte das aufbewahrungspflichtige Dokument einen neutralen Platzhalter statt des korrekten Namens** — ein Verstoß gegen die Unverändertheit/Vollständigkeit der Aufbewahrung. Daraus folgt zwingend: **gebundene Personen werden nicht anonymisiert, sondern nur eingeschränkt** (bis zum Ablauf der Aufbewahrung).

## Optionen

| Option | Mechanik | Stärke | Schwäche |
| --- | --- | --- | --- |
| **A — Hard-Delete der Person** | `DELETE FROM person` bei Löschanfrage. | Maximale Datensparsamkeit. | Bricht FKs (`RESTRICT` auf allen Profilen/Belegen), macht finalisierte Abrechnungen/Belege unauflösbar → **verletzt I2/Aufbewahrung**. Unzulässig. |
| **B — Pauschal anonymisieren** | PII immer durch Platzhalter ersetzen, FKs erhalten. | Einfach; eine Codepfad. | Anonymisiert auch gebundene Personen → finalisierte PDFs rendern falsch (s. o.). Verletzt Aufbewahrung. |
| **C — Reconciliation: Einschränkung bei Bindung, Anonymisierung ohne** | Bei Löschanfrage prüfen, ob die Person an aufbewahrungspflichtige Belege gebunden ist. **Mit** Bindung → `processing_restricted=true`, Daten behalten, maßgebliches `retention_until` erfassen, Anonymisierung erst nach Ablauf (Purge). **Ohne** Bindung → sofort anonymisieren. | Erfüllt beide Pflichten; deckungsgleich mit `architecture.md` §3.3; gebundene PDFs bleiben korrekt. | Braucht eine korrekte, getestete Bindungs-Ermittlung über mehrere Entitäten. |

## Entscheidung

**Option C.** Die Reconciliation-Logik ist eine **reine Funktion** (`app/engine/dsgvo.py`, testbar ohne DB), der Service (`app/services/dsgvo_service.py`) sammelt die Bindungen aus der DB und persistiert das Ergebnis.

### 1. Aufbewahrungsbindung (retention binding) — Definition

Eine `person` ist **aufbewahrungsgebunden**, wenn mindestens eine der folgenden Referenzen existiert (über ihre Profile bzw. als Vendor):

| Bindung | Quelle | Aufbewahrungs-Rechtsgrund |
| --- | --- | --- |
| **Vendor einer bestätigten `cost_entry`** | `cost_entry.vendor_id → vendor.person_id`, nur `status='confirmed'` | Buchungsbeleg (GoBD/§147 AO, BEG IV) |
| **Adressat einer finalisierten `settlement_run`** | `settlement_run.tenant_profile_id`/`.addressee_owner_profile_id` (über `settlement_line`), nur `status='finalized'` | finalisierte Abrechnung (I2) |
| **Mieter/Eigentümer in `lease`/`ownership` einer finalisierten Abrechnung** | finalisierte `settlement_run` → `lease_agreement_id` → `tenant_profile_id`; bzw. WEG-Run → `ownership_history` der abgerechneten Einheiten → `owner_profile_id` | Bestandteil der finalisierten Abrechnungsgrundlage |
| **Adressat einer ausgestellten `dunning_notice`** | `dunning_notice.tenant_profile_id`/`.owner_profile_id`, nur `status='issued'` | Mahnvorgang/Buchungsunterlage |

> **[§18/fachlich]** Der **genaue Umfang** dieser Liste (z. B. ob auch eine *gebuchte* `payment` als Buchungsbeleg eigenständig bindet, ob `cost_entry` schon ohne angehängten Beleg bindet) ist fachlich (StB/DSB) zu bestätigen. Die hier gewählte Liste ist konservativ-weit: im Zweifel **lieber binden (einschränken) als anonymisieren**, weil eine versehentliche Anonymisierung eines aufbewahrungspflichtigen Bezugs der schwerere Fehler ist (verletzt die Aufbewahrung irreversibel).

### 2. Maßgebliches `retention_until`

Das für die Person maßgebliche `retention_until` ist das **Maximum** der `retention_until`-Werte aller gebundenen Belege. Wo der gebundene Datensatz selbst kein `retention_until`-Feld trägt (`settlement_run`, `dunning_notice`), wird es **provisorisch** abgeleitet:

- `retention_until := (Ende des Kalenderjahres des maßgeblichen Datums) + 8 Jahre`,

mit maßgeblichem Datum = `period_end` (Abrechnung) bzw. `issue_date` (Mahnung), `invoice_date` (cost_entry, falls kein Beleg verknüpft).

> **[§18/fachlich]** Dies ist **dieselbe provisorische Annahme** wie in `m2-gobd-belegfluss.md` §7.3 (Frist beginnt mit Ablauf des Kalenderjahres, + 8 Jahre). **Welches Startdatum je Belegart gilt** (Rechnungsdatum vs. KJ-Ende vs. Festschreibedatum vs. WJ-Ende) ist in SDD §18 offen und **nicht eigenmächtig entschieden**. Die Funktion ist testbar und transparent; ihre Rechtsverbindlichkeit ist abzunehmen.

### 3. Reaktion auf eine Löschanfrage (`request_deletion`)

Immer zuerst: `deletion_requested_at := now()`, `processing_restricted := true` (die Person wird ab Antrag gegen neue aktive Verwendung gesperrt — Art. 18). Dann reconciliation-gesteuert:

- **Mit Bindung →** Daten **unter Einschränkung behalten**, **nicht** anonymisieren. `retention_until` (Maximum, §2) wird im Audit/Export ausgewiesen. Die Person bleibt für die korrekte Render-Zeit-Auflösung der gebundenen Dokumente vollständig. Anonymisierung wird erst **nach Ablauf** des maßgeblichen `retention_until` zulässig (Purge, §5).
- **Ohne Bindung →** **sofort anonymisieren** (`anonymize`, §4).

### 4. Anonymisierung (`anonymize`)

PII-Felder werden durch **neutrale, deterministische Platzhalter** ersetzt; `id` und alle strukturellen FKs (`organization_id`, Profile/Bezüge) bleiben erhalten (referenzielle Integrität, keine verwaisten Belege). `anonymized_at := now()`. Betroffene Felder von `person`:

- `first_name`, `last_name`, `company_name`, `email`, `phone`, `street`, `zip_code`, `city`, `iban`, `bic`, `bank_name`.

Platzhalter: `last_name := "Anonymisiert"`, übrige Klartextfelder `""`/neutral, Bank-/Kontaktfelder `NULL`. Die Anonymisierung ist **idempotent** (nochmaliger Aufruf ändert nichts).

> **[§18/fachlich]** Die **genaue Anonymisierungstiefe** (reicht das Neutralisieren der `person`-Felder, oder müssen z. B. Klartext-Spuren in `audit_event.payload_diff` / Notizfeldern / `snapshot_payload` mitbehandelt werden?) ist fachlich zu klären. M9 anonymisiert die `person`-Stammdaten; **frozen Snapshots werden bewusst nicht angetastet** (sie sind Belegbestandteil unter Aufbewahrung — genau deshalb wird eine gebundene Person ja nicht anonymisiert). Notiz-/Freitextfelder anderer Entitäten sind als möglicher PII-Restbestand vermerkt, aber **nicht** Teil dieser MVP-Mechanik.

### 5. Purge nach Aufbewahrungsablauf

Eine Person mit `deletion_requested_at` und Bindung wird **purge-fähig**, sobald `retention_until < heute` für **alle** Bindungen gilt: dann greift §4 (Anonymisierung) nachträglich. Der Purge ist eine separate, bewusste Operation (kein Automatismus im MVP).

Realisiert ist der Purge über die **abgesicherte `anonymize`-Operation**: Die reine Funktion `decide_deletion` liefert bei abgelaufener Bindung `purgeable_now=True` (→ `ANONYMIZE_NOW`); der Service anonymisiert dann, lehnt aber andernfalls (Bindung noch nicht abgelaufen) mit **409** ab. So ist die Aufbewahrungsfrist das harte Tor vor der Anonymisierung — derselbe Codepfad für „unbunden" und „nach Ablauf gepurgt".

> **[§18/fachlich]** Die **Auslösung** des Purge (manueller Lauf, periodischer Job, Vier-Augen-Freigabe) ist offen; M9 trifft **keine** Betriebsentscheidung darüber, sondern stellt nur die fristen-gesicherte Operation bereit. Solange die Bindung besteht, passiert nichts außer der Einschränkung.

### 6. Reconciliation-Invariante (DB-seitig garantiert durch I2)

**Aufbewahrungspflichtige Belege/Snapshots werden durch den DSGVO-Pfad NIE verändert oder gelöscht.** Das ist keine neue Zusage, sondern fällt mit ADR-0003 zusammen: `cost_entry` (confirmed), `invoice_document` (released), `settlement_run`/`settlement_line` (finalized), `payment` (booked), `dunning_notice` (issued) sind per Trigger + entzogene Grants unveränderbar. Der DSGVO-Service fasst diese Tabellen **gar nicht** mutierend an — er ändert ausschließlich `person`-Felder. Damit ist die Aufbewahrung die Rechtsgrundlage gegen den Löschanspruch, und die Render-Zeit-Auflösung gebundener Dokumente bleibt bis zum Aufbewahrungsablauf korrekt.

### 7. Auskunft / Export (`export_person_data`)

Eine reine Lese-Aggregation (Art. 15): Stammdaten + alle die Person betreffenden Referenzen (Profile, Leases, Ownership, Zahlungen, Abrechnungen als Adressat, Mahnungen, Kostenpositionen als Vendor) als strukturierte Export-Struktur. Read-only, tenant-scoped, RBAC.

### 8. Sperre neuer Verwendung

Grundsatz: Eine Person mit `processing_restricted=true` (oder `anonymized_at` gesetzt) soll **nicht** als Counterparty einer **neuen** aktiven Verwendung herangezogen werden (neuer Lease, neue Zahlung, neue Kostenposition als Vendor), **nie** rückwirkend auf bestehende, bereits festgeschriebene Belege.

In M9 implementiert ist die **Gegenrichtung**, die hier am wichtigsten ist: Eine Löschanfrage für eine Person mit **laufendem** (aktuellem/zukünftigem) Miet- oder Eigentumsverhältnis wird mit **409** abgelehnt (`has_active_relationship` im Service) — die laufende Vertragsbeziehung (Art. 6 I b) muss zuerst beendet werden. Der `processing_restricted`-Status wird gesetzt und über `status`/`export` ausgewiesen.

> **[§18/fachlich] — bewusst zurückgestellt:** Das **fail-loud-409-Blockieren beim Anlegen** neuer Leases/Zahlungen/Kostenpositionen, die eine bereits eingeschränkte/anonymisierte Person referenzieren, ist in M9 **noch nicht in die Create-Pfade (M1/M2/M6) verdrahtet**. Wo genau „neue Verwendung" zu sperren ist (nur freigegebene Verträge? auch Entwürfe? welche Entitäten?), ist eine offene Produkt-/Rechtsfrage; die Verdrahtung erfolgt nach fachlicher Festlegung des Umfangs. Der Status-Flag und die reine Entscheidung stehen dafür bereit.

## Konsequenzen

**Positiv:**
- Beide Pflichten (Aufbewahrung *und* DSGVO) werden erfüllt, ohne die jeweils andere zu verletzen.
- Die Entscheidung „einschränken vs. anonymisieren" ist eine **reine, getestete Funktion** — nachvollziehbar und prüfbar (Golden-/Reconciliation-Tests).
- Finalisierte Dokumente gebundener Personen bleiben render-korrekt bis Aufbewahrungsablauf.
- Kein neuer DB-Mutationspfad auf aufbewahrungspflichtige Tabellen → I2 bleibt unangetastet.

**Negativ / zu beachten:**
- Die Bindungs-Ermittlung muss vollständig sein; eine **vergessene** Bindungsquelle würde fälschlich anonymisieren (deshalb konservativ-weit, §1, und durch Tests abgesichert).
- Das maßgebliche `retention_until` ist **provisorisch** (KJ-Ende + 8 J.) — eine Rechtsannahme, kein Faktum.
- Restbestände von PII außerhalb von `person` (Freitext, Audit-Payload) sind **nicht** Teil dieser Mechanik (§4 [§18/fachlich]).

**Keine Selbst-Zertifizierung:** Dieses Konzept macht den Lösch-/Aufbewahrungs-Konflikt *transparent, nachvollziehbar und auditierbar* und legt alle Annahmen offen. Ob es im konkreten Fall datenschutz-/prüfungsfest ist, ist durch Datenschutzbeauftragte:n und Steuerberater:in/Fachanwalt:anwältin abzunehmen — insbesondere alle mit **[§18/fachlich]** markierten Punkte.
