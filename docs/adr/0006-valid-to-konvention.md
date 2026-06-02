# ADR-0006: valid_to-Konvention — API inklusiv, Storage exklusiv (Konvertierung an der API-Grenze)

Status: **Akzeptiert** (Entscheidung durch den Menschen getroffen; Implementierung umgesetzt — additiver Fix, keine Migration)
Datum: 2026-06-02 · Kontext: M1 ↔ M3 (Lese-/Schreib-Grenze) · Bezug: [0004-zeitanteilige-verteilung-tagegenau.md](0004-zeitanteilige-verteilung-tagegenau.md) #6 · [../m3-abrechnungs-engine.md](../m3-abrechnungs-engine.md) §3.4 · [../sdd-property-management-mvp.md](../sdd-property-management-mvp.md) §6.2, §14
Autor: `engine-correctness` (Opus 4.8)

> Diese ADR hält eine **bereits vom Menschen getroffene Entscheidung** fest und dokumentiert die umgesetzte Mechanik. Sie ist die Folge-Konsolidierung zum M3-Read-Edge-Fix (settlement_service `−1`): nachdem die *Engine*-Lese-Grenze gefixt war, macht dieser Schritt die *API*-Schreib-/Anzeige-Grenze konsistent, damit die `valid_to`-Semantik im ganzen System eine einzige, nachrechenbare Regel hat.

## Kontext

Die M1-Temporaltabellen `lease_agreement` und `ownership_history` speichern Zeiträume **halboffen** `[valid_from, valid_to)`:

- Die EXCLUDE-Constraint (M1, Migration 0003) verwendet `daterange(valid_from, valid_to, '[)')` — der obere Rand ist **exklusiv**.
- Die Punkt-in-Zeit-Abfrage (`*_repo`) verwendet `valid_from <= D AND (valid_to IS NULL OR valid_to > D)` — ebenfalls exklusiver oberer Rand.

Damit ist der gespeicherte `valid_to` der **erste NICHT belegte Tag**. Zwei aneinandergrenzende Datensätze `… valid_to = D` und `valid_from = D` überlappen in der DB **nicht**: Tag D gehört ausschließlich dem zweiten Datensatz. Das ist die saubere, btree_gist-durchsetzbare Repräsentation für „lückenlos, überschneidungsfrei" und bleibt die Grundlage der Mandanten-/Chronologie-Invarianten (SDD §14).

Zwei Probleme entstanden aus dieser Storage-Konvention an den Rändern:

1. **Engine (bereits gefixt):** Die Engine rechnet **tag-inklusiv** `[from, to]` (ADR-0004). Ohne Umrechnung las sie den gespeicherten exklusiven `valid_to` als letzten belegten Tag, zählte den Grenztag D doppelt (Vorgänger *und* Nachfolger) und warf `PeriodOverlapError` → Live-500 beim DB-legalen Fall „Auszug 30.06. / Einzug 01.07.". Gefixt durch die Lese-Grenz-Umrechnung in `settlement_service` (`valid_to − 1 Tag`).

2. **API (diese ADR):** Was trägt der **Nutzer** ein, und was sieht er zurück? Trägt er die Storage-Konvention (ersten nicht belegten Tag) ein, muss er für „Vertrag endet 30.06." den Wert `2025-07-01` eingeben — kontraintuitiv und fehleranfällig. Liest die API den rohen Speicherwert zurück, sieht er `2025-07-01`, obwohl er fachlich „bis 30.06." meint.

## Entscheidung (durch den Menschen)

**Die API ist tag-inklusiv; der Storage bleibt halboffen/exklusiv. Die Konvertierung passiert ausschließlich an der API-Grenze.**

- **Nutzer-Eingabe (inklusiv):** „Vertrag endet 30.06." → Eingabe `2025-06-30` (der **letzte belegte Tag**).
- **Storage (unverändert exklusiv):** Die DB speichert weiterhin `[valid_from, valid_to)` mit `valid_to` = erster nicht belegter Tag. **Keine Migration, kein EXCLUDE-Umbau, kein Query-Umbau.**
- **Umrechnung nur an der API-Grenze:**
  - **Schreib-Seite** (`POST /api/leases`, `POST /api/ownership`): eingehendes inklusives `valid_to` → `+1 Tag` → exklusiv speichern. `valid_from` bleibt unverändert (erster belegter Tag). `NULL` bleibt `NULL` (offen).
  - **Anzeige-Seite** (`LeaseOut`/`OwnershipOut` für Liste, Detail und den `tenant_at_date`/`owners_at_date`-Pfad): gespeicherter exklusiver `valid_to` → `−1 Tag` → inklusiv zurückgeben. Der Nutzer sieht exakt, was er eingegeben hat.
- **Engine-Lese-Grenze (unverändert):** `settlement_service` konvertiert weiterhin Storage-exklusiv → Engine-inklusiv (`−1`). **Nicht doppelt** konvertieren: Die Engine liest direkt aus dem Storage, nicht über die API.

### Präzise Regel (eine einzige Wahrheit)

Es gibt drei Repräsentationen eines oberen Zeitraumrands und genau **eine** Stelle, die zwischen ihnen umrechnet — `app/core/temporal.py`:

| Repräsentation | Semantik von `valid_to` | Wo |
| --- | --- | --- |
| **Storage (DB)** | erster NICHT belegter Tag (exklusiv, `[)`) | `lease_agreement` / `ownership_history` Tabellen |
| **API (ein/aus)** | letzter belegter Tag (inklusiv) | `leases.py` / `ownership.py` Request/Response |
| **Engine** | letzter belegter Tag (inklusiv, `[from, to]`) | `periods.py` (ADR-0004) |

Die Beziehung ist rein ±1 Kalendertag (`DATE`, kein `TIMESTAMP`; keine Zeitzone/DST):

```
storage_exklusiv      = inklusiv_letzter_tag + 1 Tag    (Schreib-Grenze)
inklusiv_letzter_tag  = storage_exklusiv      − 1 Tag    (Anzeige- und Engine-Grenze)
NULL ↔ NULL                                              (offen bleibt offen)
```

API-inklusiver Tag und Engine-inklusiver Tag sind **derselbe Kalendertag** (beide „letzter belegter Tag"); sie unterscheiden sich nur darin, *wo* die `−1`-Umrechnung erfolgt (API-Anzeige bzw. Engine-Read-Edge). Deshalb sind `db_exclusive_to_api_inclusive` und `db_exclusive_to_engine_inclusive` dieselbe Rechnung — nur unter zwei Namen, damit jede Aufrufstelle ihre Absicht selbst dokumentiert.

### Nicht betroffen

- **`cost_entry.service_period_from/_to` (M2):** fachlich **inklusiv**, **kein** `[)`-EXCLUDE. Wird **nie** umgerechnet (ADR-0004 #6, m3 §3.4).
- **`at_date`-Query-Parameter** (`tenant_at_date`/`owners_at_date`): ein **Zeitpunkt**, kein Zeitraum-Oberrand. Die halboffene Punktabfrage `valid_from <= D AND valid_to > D` liefert für jeden Kalendertag D korrekt den an D belegenden Datensatz — unabhängig von der Inklusiv-/Exklusiv-Konvention. `at_date` wird daher **nicht** umgerechnet; nur der **ausgegebene** `valid_to` (über `_to_out`) ist inklusiv.
- **Settlement-Period-Grenzen** (`period_start`/`period_end` aus der UI): bereits inklusiv, kein `[)`. Unverändert.

## Konsequenzen

**Positiv:**
- **Intuitive API:** „endet 30.06." ↔ `2025-06-30`. Roundtrip-stabil: Was reingeht, kommt zurück.
- **Storage unangetastet:** Die btree_gist-EXCLUDE und die Chronologie-Invariante (SDD §14) bleiben exakt wie sie sind — kein Migrationsrisiko, kein Anfassen der durchgesetzten Mandanten-/Unveränderbarkeits-Mechanik.
- **Aneinandergrenzend funktioniert ohne Sonderfall:** „A bis 30.06." + „B ab 01.07." → Schreib-Grenze mappt A.valid_to auf `2025-07-01` = B.valid_from → `[.,2025-07-01)` und `[2025-07-01,.)` überlappen unter `[)` nicht (kein falscher 409). Die Engine-Lese-Grenze (`−1`) macht daraus wieder A=…30.06., B=01.07.… → Σ Zeitanteile = 1, kein Phantom-Tag (ADR-0004 #4). Schreib-`+1` und Engine-`−1` heben sich exakt auf.
- **Eine Wahrheit:** Alle ±1-Umrechnungen leben in `app/core/temporal.py`; Engine-Service und beide APIs importieren von dort. Kein „mal +1, mal −1" verstreut.

**Negativ / zu beachten:**
- **Doppelkonvertierung wäre ein Bug:** Die Engine liest aus dem **Storage** (nicht über die API), wendet also ihre eigene `−1` an. Würde die Engine je über die (bereits inklusive) API lesen, entstünde ein Off-by-one. Per Konstruktion liest sie roh aus den Tabellen; der Off-by-one-Wächter-Test fixiert beide Enden.
- **Update-Pfad:** Phase-1-APIs für Lease/Ownership haben nur `create` (kein `update`-Endpoint). Käme ein `update` hinzu, **muss** es dieselbe Schreib-Umrechnung anwenden — die zentrale Hilfsfunktion ist dafür vorhanden.
- **Bestands-/Importdaten:** Daten, die unter Umgehung der API direkt in die DB geschrieben werden (Seeds, Migrationen, künftige Bulk-Importe), müssen die Storage-Konvention (exklusiver `valid_to`) selbst einhalten — die API-Umrechnung greift dort nicht. Die bestehenden Seeds tun dies bereits explizit (`test_m3_settlement.py`).

**Nicht verhandelbar (Folge dieser ADR):**
1. Storage bleibt halboffen `[valid_from, valid_to)`; `valid_to` = erster nicht belegter Tag. Keine Migration.
2. Die ±1-Umrechnung existiert an genau **einer** Code-Stelle (`app/core/temporal.py`) und wird pro Datenfluss **genau einmal** angewandt (Schreib-Grenze einmal `+1`; Anzeige-Grenze einmal `−1`; Engine-Read-Edge einmal `−1`).
3. `service_period_*` und `at_date` werden **nie** umgerechnet.

## Test-Nachweis

Über die echten Endpunkte (`tests/test_valid_to_convention.py`):

- **Roundtrip:** Lease „endet 2025-06-30" via API anlegen → POST-Echo und GET liefern `valid_to=2025-06-30`; direkte DB-Prüfung zeigt `2025-07-01`. Analog für Ownership.
- **Aneinandergrenzend (end-to-end):** „A bis 2025-06-30" + „B ab 2025-07-01" via API anlegbar (kein 409); danach `compute` → A=01.01.–30.06. (181 T), B=01.07.–31.12. (184 T), 181+184=365, A+B-Kosten = volle 1200,00 € (kein Phantom-Tag, kein Cent verloren). Beweist, dass Schreib-`+1` und Engine-`−1` korrekt komponieren.
- **Off-by-one-Wächter:** Ein Test fixiert beide Enden gegen ein POST: ohne die Schreib-`+1` wird der DB-Assert (`== 2025-07-01`) rot; ohne die Anzeige-`−1` wird der GET-Assert (`== 2025-06-30`) rot. Beide Richtungen wurden durch temporäres Entfernen der jeweiligen Konvertierung als rot verifiziert.
- **Helfer-Unit-Tests:** `+1`/`−1`, NULL-Erhaltung, Schaltjahr-/Jahresgrenze, Roundtrip-Identität, Engine-Alias == Anzeige-Alias.

Die bestehende Engine-Read-Edge-Regression (`TestM3AdjacentLeaseReadEdge`) und der bestehende Helfer-Test (`db_exclusive_to_engine_inclusive`) bleiben unverändert grün; der Helfer wird jetzt aus `app/core/temporal.py` re-exportiert (Importpfad `app.services.settlement_service` erhalten).

> **Keine Selbst-Zertifizierung:** Die Inklusiv-Eingabe entspricht dem fachlich üblichen Verständnis („Vertrag endet am 30.06."). Ob in einem konkreten Vertrag eine abweichende Stichtagsabrede gilt, bleibt fachlich (Steuerberater/Fachanwalt) abzunehmen. Diese ADR legt die *technische* Konvention und ihre nachrechenbare Umsetzung fest.
