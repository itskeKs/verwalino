# ADR-0011: Beschluss-Sammlung -- Unveraenderbarkeit und lueckenlose Nummerierung

Status: **Angenommen**
Datum: 2026-06-02 -- Kontext: M8 -- Bezug: SDD section 6.10, WEG section 24 Abs. 7, ADR-0003

## Kontext

Die Beschluss-Sammlung (WEG section 24 Abs. 7) ist ein gesetzlich vorgeschriebenes,
fortlaufend nummeriertes Register aller Beschluesse einer Eigentuemer-
gemeinschaft. Jeder Eintrag enthaelt Datum, Ort, Wortlaut und ggf. einen
Anfechtungs-/Aufhebungsvermerk. Die Sammlung ist oeffentlich einsehbar
(fuer Eigentuemer und deren Rechtsnachfolger).

Zwei Invarianten muessen DB-erzwungen sein:
1. **Lueckenlose, fortlaufende Nummerierung** je WEG (Property).
2. **Unveraenderbarkeit** des einmal eingetragenen Wortlauts, der Nummer,
   des Datums, des Orts und des Beschluss-Bezugs. Der einzige erlaubte
   Nachtrag ist der Anfechtungs-/Aufhebungsvermerk.

## Entscheidung

### Nummerierung

- `sequence_number` wird in der Anlage-Transaktion als `max(seq WHERE property_id) + 1` vergeben.
- `UNIQUE (organization_id, property_id, sequence_number)` verhindert Duplikate.
- Lueckenlosigkeit ergibt sich daraus, dass nur die Service-Schicht Eintraege
  anlegt und immer max+1 verwendet. DELETE ist per Trigger verboten, daher
  koennen keine Luecken durch Loeschen entstehen.
- Bei Konkurrenz (zwei gleichzeitige INSERTs) schlaegt der UNIQUE-Constraint
  bei einer der beiden fehl -> IntegrityError, Retry.

### Unveraenderbarkeit

Mechanik: **BEFORE UPDATE / BEFORE DELETE Trigger** (ADR-0003 Muster), aber
mit einer strengeren Variante: die Einfrierung gilt **ab INSERT**, nicht ab
einem Status-Uebergang. Es gibt keinen "draft"-Status fuer Register-Eintraege.

Eingefrorene Spalten (jede Aenderung -> RAISE EXCEPTION):
- `organization_id`, `property_id`, `sequence_number`, `entry_date`,
  `location`, `wording`, `resolution_id`, `created_at`

Erlaubte Mutationen:
- `challenge_annotation` (Anfechtungs-/Aufhebungsvermerk) -- kann jederzeit
  gesetzt, aktualisiert oder geloescht werden.
- `updated_at` (technischer Zeitstempel)

DELETE ist IMMER verboten (Trigger + keine DELETE-Grant fuer app_rw als
Defense-in-Depth, konsistent mit ADR-0003).

## Konsequenzen

**Positiv:**
- Die gesetzliche Anforderung an die Beschluss-Sammlung ist DB-erzwungen,
  nicht nur Konvention.
- Ein Bug im Service oder ein Ad-hoc-UPDATE kann den Wortlaut oder die
  Nummerierung nicht korrumpieren.
- Beweisbar per Negativtest (UPDATE auf Wortlaut/Nummer schlaegt fehl,
  DELETE schlaegt fehl, Anfechtungsvermerk-Update gelingt).

**Negativ / zu beachten:**
- Eine fehlerhafte Eintragung (z. B. Tippfehler im Wortlaut) kann nur durch
  einen Korrekturvermerk im challenge_annotation-Feld dokumentiert werden,
  nicht durch Ueberschreiben. Dies entspricht dem papiergebundenen Vorbild.
- Migrations, die Register-Daten anfassen, sind ein Review-rotes Tuch
  (wie bei allen ADR-0003-geschuetzten Tabellen).
