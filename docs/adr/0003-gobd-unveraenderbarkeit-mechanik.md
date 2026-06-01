# ADR-0003: Erzwingung der GoBD-Unveränderbarkeit (I2) — Status-bewusster Trigger + entzogene Grants

Status: **Vorgeschlagen** (Bestätigung durch den Menschen ausstehend — noch kein Code/Commit)
Datum: 2026-06-01 · Kontext: M2 · Bezug: [../architecture.md](../architecture.md) §3.2(e), §5.4 · [m2-gobd-belegfluss.md](../m2-gobd-belegfluss.md) · SDD §6.6, §9, §14, §16

> Diese ADR entscheidet den in `architecture.md` §3.2(e) und §10 bewusst offen gelassenen Punkt: *„Endgültige Wahl des Unveränderbarkeits-Erzwingungsmechanismus (Trigger vs. Grants vs. frozen-Tabelle) — Detail in M2."*

## Kontext

Invariante **I2** (GoBD-Unveränderbarkeit, SDD §6.6/§14): Ein **freigegebener** Beleg und eine **finalisierte** Abrechnung dürfen inhaltlich nie überschrieben oder gelöscht werden; eine Korrektur erzeugt eine **neue Version** mit Bezug zum Original. Das berührt zwei neue M2-Tabellen:

- **`cost_entry`** — Kostenposition. Sie ist im Status `draft_extracted`/`needs_review` **bewusst editierbar** (der Nutzer korrigiert OCR-Vorschläge), wird aber mit `confirmed` **eingefroren** (abrechnungsrelevant, SDD §9). Sie ist also *nicht* durchgehend append-only — sie hat einen klaren Einfrier-Zeitpunkt.
- **`invoice_document`** — die eigentliche Belegdatei (Metadaten + SHA-256 + Versionskette). Eine `active`-Version, die freigegeben (`released_at` gesetzt) wurde, ist append-only; Korrektur = neue Zeile mit `supersedes_id`.

Der bestehende Mechanismus für `audit_event` (M0) ist **entzogene UPDATE/DELETE-Grants** für `app_rw` plus eine weggelassene DELETE-Policy — abgesichert durch `test_rls_coverage.py::test_audit_event_no_update_delete_for_app_rw`. Das funktioniert für eine **durchgehend** append-only Tabelle. Es funktioniert **nicht** unverändert für `cost_entry`, weil dort UPDATE auf *Entwurfs*-Zeilen erlaubt sein muss und nur ab `confirmed` verboten ist. Eine pauschale Grant-Entziehung kann „veränderbar bis Freigabe, danach eingefroren" nicht ausdrücken.

## Optionen

| Option | Mechanik | Stärke | Schwäche |
| --- | --- | --- | --- |
| **A — Entzogene Grants (wie `audit_event`)** | `REVOKE UPDATE, DELETE` für `app_rw`. | Härteste, einfachste Garantie; kein Trigger-Code; bereits erprobt. | Kennt den Zeilen-Status **nicht** → kann „editierbar bis `confirmed`" nicht abbilden. Für `cost_entry` allein unbrauchbar. |
| **B — BEFORE UPDATE/DELETE-Trigger, status-bewusst** | PL/pgSQL-Trigger: erlaubt definierte Übergänge, verbietet jede Mutation inhaltstragender Spalten, sobald die Zeile eingefroren ist; verbietet DELETE eingefrorener Zeilen. | Drückt die Statusmaschine **in der DB** aus; gilt auch für die Migrator-Rolle, wenn nicht bewusst umgangen; eine Stelle für die Regel. | Trigger-Logik muss getestet werden; lässt sich von einer Rolle mit `session_replication_role = replica` theoretisch deaktivieren (nur Superuser/Owner). |
| **C — Separate „frozen"-Tabelle** | Bei Freigabe Zeile in eine zweite Tabelle ohne UPDATE/DELETE-Grant kopieren. | Physische Trennung veränderlich/eingefroren. | Doppeltes Schema, Daten-Duplikation, FK-/Query-Komplexität (Belege liegen in zwei Tabellen); für ein MVP unverhältnismäßig; Konsistenz zweier Tabellen ist eigene Fehlerquelle. |

## Entscheidung

**Kombination B (führend) + A (Backstop), nach Tabelle differenziert:**

1. **`cost_entry` und `invoice_document`: status-bewusster `BEFORE UPDATE` / `BEFORE DELETE`-Trigger (Option B)** als primäre Erzwingung. Der Trigger ist die einzige Stelle, an der „was darf sich in welchem Status noch ändern" definiert ist:
   - **`cost_entry`** (Einfrier-Bedingung: `status = 'confirmed'`):
     - UPDATE einer `confirmed`-Zeile, das eine **inhaltstragende Spalte** ändert (Betrag, USt, Kategorie, Lieferant, Datum, Leistungszeitraum, Verteilerschlüssel, Umlagefähigkeit, Objekt/Mandat) → `RAISE EXCEPTION`.
     - Der **einzige** zulässige UPDATE einer `confirmed`-Zeile ist der Übergang `confirmed → superseded` durch eine Korrektur-Neuversion (siehe Belegfluss-Doc §3) bzw. das reine Setzen eines technischen „abgelöst"-Markers — der Trigger lässt genau diese Spaltenänderung zu und keine andere.
     - DELETE einer `confirmed`-Zeile → immer `RAISE EXCEPTION`.
     - UPDATE/DELETE im Status `draft_extracted`/`needs_review`/`rejected` (Entwurf) → erlaubt.
   - **`invoice_document`** (Einfrier-Bedingung: `released_at IS NOT NULL`):
     - UPDATE einer freigegebenen Zeile, das `content_sha256`, `storage_uri`, `storage_version`, `version`, `released_at`, `released_by` oder `content_byte_size` ändert → `RAISE EXCEPTION`.
     - Erlaubt bleibt **ausschließlich** der Übergang `status` von `active` → `superseded` (wenn eine Nachfolgeversion eingehängt wird) und das Setzen von `superseded_at`; jede andere Spaltenänderung einer freigegebenen Zeile wird abgewiesen.
     - DELETE einer freigegebenen Zeile → immer `RAISE EXCEPTION`.
   - Die genaue Spaltenliste („inhaltstragend" vs. „technisch zulässig") wird in der Migration als Trigger-Quelltext fixiert und ist Teil des Code-Reviews.

2. **Grant-Backstop (Option A) dort, wo es passt:** Für `invoice_document` wird zusätzlich erwogen, `app_rw` **kein DELETE** zu gewähren (`GRANT SELECT, INSERT, UPDATE` ohne `DELETE`) — Belegdateien werden nie gelöscht, nur abgelöst. Das ist eine zweite, vom Trigger unabhängige Linie gegen Beleg-Löschung. **[ENTSCHEIDUNG]** ob DELETE auf `invoice_document` für `app_rw` komplett entzogen wird (empfohlen: ja) oder nur per Trigger geblockt wird. Für `cost_entry` wird DELETE **nicht** pauschal entzogen (Entwürfe/`rejected` dürfen gelöscht werden), sondern allein per Trigger statusabhängig kontrolliert.

3. **`settlement_run`/`settlement_line` (ab M3/M4):** dieselbe Trigger-Mechanik mit Einfrier-Bedingung `status = 'finalized'`. In diesem ADR vorgemerkt, damit M3/M4 dasselbe Muster wiederverwenden und nicht neu erfinden.

4. **`audit_event` bleibt unverändert bei Option A** (durchgehend append-only, kein Status) — nicht anfassen.

**Begründung:** Die GoBD-Unveränderbarkeit ist hier **status-bedingt** („eingefroren ab Freigabe"), nicht absolut. Nur ein status-bewusster Trigger kann diese Semantik in der DB selbst ausdrücken; entzogene Grants allein können es nicht. Wo Unveränderbarkeit *absolut* ist (Belegdatei nie löschen, `audit_event` nie ändern), ergänzt die härtere Grant-Entziehung den Trigger als zweite Linie (Defense-in-Depth, konsistent zur RLS-Philosophie aus ADR-0001). Option C (frozen-Tabelle) bringt für ein MVP nur Duplikations- und Konsistenzkosten ohne Mehrwert gegenüber Trigger+Grant.

## Konsequenzen

**Positiv:**
- Die Statusmaschine (Belegfluss-Doc §3) ist nicht nur Anwendungs-Konvention, sondern **DB-erzwungen**: ein Bug im Service oder ein Ad-hoc-`UPDATE` kann eine `confirmed`-Kostenposition oder einen freigegebenen Beleg nicht inhaltlich verändern.
- Korrektur-als-Neuversion wird zur einzigen möglichen Korrekturform — genau wie SDD §14 verlangt.
- Konsistentes Muster für alle finalisierten Entitäten (Belege jetzt, Abrechnungen ab M3/M4).
- Beweisbar per Tests (siehe Belegfluss-Doc §8): „UPDATE/DELETE auf `confirmed` schlägt fehl", „Korrektur erzeugt neue Version", „Hash-Verifikation".

**Negativ / zu beachten:**
- Trigger-Logik ist Code in der DB und muss versioniert (Alembic), reviewt und **getestet** werden (Positiv- *und* Negativtests: erlaubter Entwurfs-Edit vs. abgewiesener `confirmed`-Edit).
- Ein Trigger kann theoretisch durch `SET session_replication_role = replica` umgangen werden — das erfordert aber Superuser-/Owner-Rechte, die `app_rw` nicht hat (ADR-0001). Migrations-/Wartungsjobs (`app_migrator`) müssen die Unveränderbarkeit bewusst respektieren; eine spätere Migration, die finalisierte Daten anfasst, ist ein Review-rotes Tuch.
- Der Trigger muss die „technisch zulässige" Spaltenänderung (`active → superseded`) präzise von „inhaltlicher Mutation" abgrenzen. Diese Abgrenzung ist der heikelste Teil und wird in der Migration explizit kommentiert.

**Nicht verhandelbar (über alle finalisierten Entitäten):**
1. Inhaltstragende Spalten einer eingefrorenen Zeile sind unveränderbar — DB-erzwungen, nicht nur per Konvention.
2. Korrektur ausschließlich über Neuversion mit `supersedes_id`/Versionskette; das Original bleibt erhalten und wird `superseded` markiert.
3. Belegdateien werden nie gelöscht (nur abgelöst); `retention_until` ist der einzige Anker, an dem nach Ablauf überhaupt über Entfernung entschieden wird (DSGVO-Verzahnung, Belegfluss-Doc §7).
4. Jede DB-erzwungene Unveränderbarkeit ist durch einen Negativtest belegt, der ohne den Trigger/Grant rot würde.
