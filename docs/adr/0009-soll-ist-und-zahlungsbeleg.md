# ADR-0009: Soll-Position abgeleitet (nicht persistiert) · Zahlung als unveränderbarer Beleg (Storno statt Edit)

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-02 · Kontext: M6 · Bezug: [../m6-zahlungen-mietkonto.md](../m6-zahlungen-mietkonto.md) §1, §2, §3, §5 · [0003-gobd-unveraenderbarkeit-mechanik.md](0003-gobd-unveraenderbarkeit-mechanik.md) (wiederverwendetes Muster) · [0005-rundung-largest-remainder.md](0005-rundung-largest-remainder.md) · [0007-weg-abrechnungsspitze-und-wirtschaftsplan.md](0007-weg-abrechnungsspitze-und-wirtschaftsplan.md) · [../architecture.md](../architecture.md) §2.6, §3, §5.1 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.3, §6.7, §14, §16, §17, §18
Autor: `engine-correctness` (Opus 4.8)

> Diese ADR entscheidet zwei zusammenhängende M6-Kernfragen: (1) ob die **Soll-Position** (`open_item`) **abgeleitet** (zur Laufzeit berechnet) oder **persistiert** (materialisiert) ist, und (2) ob eine erfasste **Zahlung** den GoBD-Belegcharakter trägt und damit **unveränderbar** ist (Korrektur = Storno + Neubuchung) oder editierbar. Beide sind **[ENTSCHEIDUNG]** für den Menschen. Offene fachliche/§18-Punkte sind als **[§18/fachlich]** markiert.

## Kontext

M6 bildet den Geldfluss ab (SDD §6.3, §6.7): manuell erfasste Zahlungen, ein einfaches Mietkonto (Soll-Stellung je Periode, erfasste Zahlungen, Saldo) und eine Offene-Posten-Liste je Person. **Bankimport (CAMT/MT940) und automatischer Abgleich sind explizit Nicht-Ziel (SDD §3).**

Zwei Größen stehen sich gegenüber:

- **Soll** — was eine Person für eine Periode schuldet: beim Mieter Kaltmiete + NK-Vorauszahlung je Monat (aus `lease_agreement`), beim Eigentümer der monatliche Hausgeld-Vorschuss (aus dem finalisierten `economic_plan`, `OWNER_ADVANCE.monthly_amount`). Maßstab monatlich (ADR-0007 E4-3b für den Plan; konsistent fortgeführt für die Miet-Sollstellung).
- **Ist** — was die Person tatsächlich gezahlt hat: die erfassten `payment`-Zeilen.

Der **offene Posten** (`open_item`) ist die Verknüpfung: Soll-Position, der zugeordnete Ist-Betrag und der Restsaldo. SDD §13 nennt `OpenItem` als Entität; offen ist, ob es ein **berechnetes** oder ein **gespeichertes** Objekt ist. SDD §18 nennt zudem ausdrücklich offen: „Detailtiefe des Mietkontos (nur Soll-Ist vs. einzelne Sollstellungen je Monat)".

### Frage 1 — Soll abgeleitet vs. persistiert

| Option | Mechanik | Bewertung |
| --- | --- | --- |
| **A — Soll abgeleitet (berechnet)** | Die Soll-Positionen einer Periode werden bei Bedarf aus `lease_agreement`/`economic_plan` **berechnet** (reine Funktion, monatlicher Maßstab); `open_item` ist eine berechnete Sicht (Soll − zugeordnetes Ist je Periode). **Nichts** wird je Monat materialisiert. | Eine Quelle der Wahrheit (Vertrag/Plan); keine Drift zwischen gespeichertem Soll und geänderten Stammdaten; kein Scheduler/Cron, der monatlich Zeilen erzeugt; einfacher GoBD-Footprint (keine zusätzliche unveränderbar zu haltende Tabelle). Nachteil: pro-Monat-Mahnstatus (M7) braucht trotzdem einen Anker (siehe unten). **Empfohlen.** |
| **B — Soll persistiert (materialisiert)** | Ein Job erzeugt je Monat/Mietvertrag bzw. Eigentümer eine `open_item`-Zeile (Soll), die dann gegen Zahlungen abgeglichen wird. | Stabiler Anker für Mahnstatus je Monat; aber: zweite, pflegebedürftige Wahrheit (wird bei Stammdatenänderung — rückwirkende Mietänderung, korrigierter Plan — schnell inkonsistent); braucht einen zuverlässigen Erzeugungsjob; GoBD-Frage „ist eine materialisierte Sollstellung ein einzufrierender Beleg?" entsteht zusätzlich. Mehr bewegliche Teile für ein MVP. |

### Frage 2 — Zahlung unveränderbar (Storno) vs. editierbar

| Option | Mechanik | Bewertung |
| --- | --- | --- |
| **U — Zahlung unveränderbar; Korrektur = Storno + Neubuchung** | Eine erfasste, „verbuchte" Zahlung ist append-only. Tippfehler/falsche Zuordnung → **Storno-Zahlung** (gegenläufiger Eintrag mit Bezug zum Original) + ggf. korrekte Neubuchung. Muster wie ADR-0003 (Trigger, Einfrier-Bedingung). | Zahlungen sind **Buchungsbelege** (GoBD, SDD §6.6/§17, 8-Jahres-Aufbewahrung). Ein Buchungsbeleg wird nicht überschrieben; die Korrektur ist nachvollziehbar (wer/wann/warum). Konsistent zur projektweiten I2-Invariante und zum bestehenden `cost_entry`/`settlement_run`-Muster. **Empfohlen.** |
| **E — Zahlung frei editierbar** | UPDATE der Betrag-/Datum-/Zuordnungsfelder erlaubt. | Verletzt die GoBD-Unveränderbarkeit eines Buchungsbelegs; eine zugeordnete Zahlung, die nachträglich verändert wird, kann eine bereits **finalisierte** Abrechnung (die diese Zahlung als `DERIVED`-Ist herangezogen hat) im Nachhinein widersprüchlich machen. Verworfen. |

## Entscheidung

**(1) Soll-Position abgeleitet (Option A).** *(Empfehlung, [ENTSCHEIDUNG E6-1])*

- Die Soll-Positionen einer Person/Periode werden aus den Stammdaten **berechnet**, nicht je Monat materialisiert. Eine reine Engine-Funktion erzeugt aus `lease_agreement` (Kaltmiete + NK-Vorauszahlung, tagegenau angeschnitten bei unterjährigem Beginn/Ende, monatlicher Maßstab) bzw. aus dem finalisierten `economic_plan` (`OWNER_ADVANCE.monthly_amount`) die fälligen Soll-Beträge je Monat (`expected_charge`-Liste).
- `open_item` ist die **berechnete Saldo-Sicht** je (Person, Periode, Typ): `offen = Soll − zugeordnetes Ist`. Es ist **keine** je Monat persistierte Zeile.
- **M7-Vorgriff bewusst vermieden, aber Tür offen:** Mahnstufen/-historie (M7) brauchen einen pro-Posten-Anker. Damit M6 das nicht eigenmächtig vorwegnimmt: M6 liefert die *berechneten* offenen Posten; ob M7 daraus einen kleinen **persistierten Mahn-/Status-Datensatz je überfälligem Posten** materialisiert (mit Bezug auf die berechnete Soll-Periode), ist **[§18/fachlich F6-1]** und Sache von M7. M6 entscheidet die Mahn-Persistenz **nicht**.

**(2) Zahlung unveränderbar; Korrektur = Storno + Neubuchung (Option U).** *(Empfehlung, [ENTSCHEIDUNG E6-2])*

- Eine `payment`-Zeile ist nach dem „Verbuchen" (Status `booked`) ein **unveränderbarer Buchungsbeleg**. Inhaltstragende Spalten (Betrag, Richtung, Datum, Bankkonto, Gegenpartei, Zweck/Typ, Zuordnung) sind dann eingefroren.
- **Korrektur = Storno + Neubuchung:** Ein Storno ist eine neue `payment`-Zeile mit gegenläufigem Vorzeichen/Markierung (`reverses_id` → Original), die den Effekt der Originalzahlung neutralisiert; die korrekte Buchung ist eine weitere neue Zeile. Das Original bleibt unverändert erhalten (GoBD, SDD §14).
- **Mechanik = ADR-0003-Muster, wiederverwendet, nicht neu erfunden:** status-bewusster `BEFORE UPDATE/DELETE`-Trigger mit Einfrier-Bedingung `status='booked'` (analog `cost_entry.status='confirmed'` / `settlement_run.status='finalized'`). Im Status `draft` (vor dem Verbuchen) ist eine Zahlung frei editier-/löschbar (Erfassungs-Korrektur), exakt wie ein `cost_entry`-Entwurf. Einzig erlaubte Mutation einer `booked`-Zeile: das Setzen von `reversed_by_id` (Storno-Kette, analog `superseded_by_id`).
- **Begründung der `draft`/`booked`-Zweiteilung:** Die reine Erfassung (Tippen) muss korrigierbar sein, ohne sofort einen Storno-Beleg zu erzeugen — sonst wird jede Tipp-Korrektur zu einem GoBD-Vorgang. Der Einfrier-Punkt ist das bewusste „Verbuchen", analog zum `confirm` einer Kostenposition (m2). **[ENTSCHEIDUNG E6-3]** ob M6 diese Zweiteilung führt oder eine Zahlung **sofort** `booked` ist (kein Entwurf). Empfehlung: Zweiteilung (`draft`→`booked`), weil sie das etablierte Muster spiegelt und die Erfassungs-UX nicht in GoBD-Storni zwingt.

**Wechselwirkung mit finalisierten Abrechnungen (der eigentliche Grund für die Unveränderbarkeit):** Eine finalisierte BK-/WEG-Abrechnung friert ihren `advance_payments_total`/Ist als **Kopie** in den Snapshot ein (M3 §1.2, M4 §1.5). Eine spätere Stornierung/Korrektur einer Zahlung verändert die **finalisierte** Abrechnung daher **nicht** (der Snapshot bleibt unveränderbar). Die Korrektur wirkt erst auf die **nächste** (Storno-/Neuberechnungs-)Abrechnung (`create_correction_run`). So bleibt die Kette in sich konsistent: unveränderbare Zahlung → unveränderbarer Abrechnungs-Snapshot → Korrektur stets als neue Version.

## Konsequenzen

**Positiv:**
- **Eine Quelle der Wahrheit fürs Soll** (Vertrag/Plan); keine Drift, kein Materialisierungs-Job, kein „verwaistes" Soll nach Stammdatenänderung.
- **Zahlung als prüffester Buchungsbeleg**: append-only, DB-erzwungen (ADR-0003-Muster), 8-Jahres-Aufbewahrung sauber (Zahlung trägt `retention_until`-Anker analog `invoice_document`).
- **Konsistenz mit dem bestehenden System**: `advance_payments_source='DERIVED'` (M3/M4 vorbereitet) zieht die Ist-Summe aus genau diesen unveränderbaren, eindeutig typisierten Zahlungen; keine Doppelzählung, weil jede Zahlung genau einem Soll-Typ/Adressat/Periode zugeordnet ist (m6 §6).
- **Snapshot-Integrität bleibt gewahrt**: finalisierte Abrechnungen sind gegen nachträgliche Zahlungskorrekturen immun (sie tragen die eingefrorene Ist-Kopie).

**Negativ / zu beachten:**
- Abgeleitetes Soll bedeutet: der **Stand** des Mietkontos zu einem Zeitpunkt ist eine berechnete Größe; die Berechnungsregel (monatlicher Maßstab, tagegenaue Anschnitte bei Vertrags-Beginn/-Ende, Fälligkeitstag) muss **deterministisch und getestet** sein (m6 §2, Golden-Tests m6 §7), sonst „springt" der Saldo.
- Die `draft`/`booked`-Zweiteilung verlangt einen bewussten „Verbuchen"-Schritt in der UI (analog `confirm`); das ist gewollt, aber ein zusätzlicher Klick.
- Storno-statt-Edit ist für Nutzer ungewohnter als „Zeile ändern"; die UI muss Storno + Neubuchung als geführten Vorgang anbieten (sonst Frustration). Fachlich aber alternativlos für einen Buchungsbeleg.

**Nicht verhandelbar (Folge dieser ADR):**
1. Das Soll ist **abgeleitet** aus Vertrag/Plan; es gibt keine zweite, materialisierte Soll-Wahrheit, die driften könnte (M6).
2. Eine **verbuchte** Zahlung ist ein unveränderbarer Buchungsbeleg; Korrektur ausschließlich über **Storno + Neubuchung** mit Bezug zum Original (`reverses_id`/`reversed_by_id`), DB-erzwungen (ADR-0003-Trigger, Einfrier-Bedingung `status='booked'`).
3. Eine **finalisierte** Abrechnung wird durch spätere Zahlungskorrekturen nie verändert (sie trägt die eingefrorene Ist-Kopie); die Korrektur wirkt nur auf eine neue Abrechnungsversion.
4. Jede DB-erzwungene Unveränderbarkeit ist durch einen Negativtest belegt, der ohne den Trigger rot würde (m6 §7).

> **[§18/fachlich] Keine Selbst-Zertifizierung:** Diese ADR macht Soll, Ist, offene Posten und die Belegunveränderbarkeit *transparent, deterministisch und nachrechenbar* und legt die getroffenen Annahmen offen. Ob die Mietkonto-Detailtiefe, die Behandlung von Kaution/Kautionskonto und die GoBD-Belegqualität einer manuell erfassten Zahlung im konkreten Fall prüfungsfest sind, ist fachlich (Steuerberater/Fachanwalt) abzunehmen. Diese ADR legt die *technische* Definition fest, nicht die abschließende Rechtsbeurteilung.
