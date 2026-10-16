# 04a-replacement-outcomes — Package 4

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 03c-intake-runtime. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Gespeichertes unakzeptiertes 0123-Inkrement qualifizieren: deferred-seal/Antwort-Semantik diagnostizieren, Seal nicht schwächen. Inaccessible intermediate/replaced-copy, historische Ketten und UI prüfen. Deploymenthead erst nach lokaler Abnahme anheben.

## Wiederaufnahme dieses konkreten Fehlers

Zuerst `../receipts/04a-replacement-outcomes.md` auf den aktuellen Teststand prüfen.
Der unveränderte Regressionstest heißt
`test_replacement_completion_live_audience_and_atomic_seal` in
`backend/tests/test_replacement_exception_outcomes.py`.

Belegte Ursache: `seal_exception_completion` ist ein
`DEFERRABLE INITIALLY DEFERRED` Constraint-Trigger (Migration 0110, in 0123
verschärft), kein gewöhnlicher erst beim Commit geprüfter CHECK. ORM-`flush()`
erzwingt diesen Trigger nicht. Der sichere lokale Fix prüft ihn mit
`SET CONSTRAINTS seal_exception_completion IMMEDIATE` im Completion-Service
und stellt danach `DEFERRED` wieder her. `get_db()` bleibt Transaktionsbesitzer;
SQLSTATE 23514 wird vor der Response zu 422, der Dependency-Kontext rollt zurück.

Nicht erneut versuchen: Service-Commit (verliert SET LOCAL Tenantkontext),
Rollback-only-Dependency (verliert normale Writes), globale ASGI-Middleware
(zweite Response nach Response-Start), bloßes `flush()` für deferred Trigger.

Abnahmematrix: vollständige Replacement-Outcomes-Datei (historische Ketten,
intermediate/replaced-copy, SQLSTATE/Retry/Org-/Scope-Denial), bestehende
Exception-Completion/Allocation-Regressionen, Intake-Worker-Unterbrechung,
Frontend test/lint/build. Assertions, Erwartungen, Testanzahl und Testumfang
unverändert lassen. Operatorfreigabe vom 2026-10-11 erlaubt ausschließlich die
Korrektur aller historischen Migrationstest-Setups für den aktiven Head; neuere
Revisionen vor älteren geordnet zurückbauen und bei vollständigem Wiederaufbau
wiederherstellen. Akzeptierte Migrationen und Retention-Checks nicht ändern.
Breitere Suite zusätzlich belegen; externe/unabhängige Fehler separat mit
konkreter Signatur erfassen.
Keine Abnahme behaupten, solange ein vorgeschriebener lokaler Check offen ist.
Keine neue UI/Browser-Integration implementiert: vorhandene UI prüfen;
Vitest nicht als echte Browserjourney ausgeben.

## Gezielt zu suchende Einstiegspfade

- `docs/2026-10-03-paused-implementation-checkpoint.md`
- `backend/migrations/versions/0123_replacement_exception_outcomes.py`
- `backend/tests/test_replacement_exception_outcomes.py`
- `backend/app/services/exception_service.py`
- `frontend/src/components/intake/ExceptionCompletionPanel.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**B3 — Assignment, duplicates, splits and shared exceptions.** P1.1 after B2.
Add **new** `assignment_proposal`, `mapping_version`, `exception_case`,
`document_cost_link` / allocation-set models and services,
`api/intake_items.py`, `api/exceptions.py`, `services/assignment_service.py`,
`services/document_allocation_service.py`; extend cost confirmation through this
adapter. Add **new** `ExceptionDetailPage.tsx` and source/proposal review components.
Resolve stable IDs/address/context with reasons; hints cannot override contrary
evidence. Version learned mappings only after opt-in. Hash/business duplicates,
credit notes, line splits and replacements require explicit resolutions.
Deduplicate exceptions by scope/cause/source revision; advisory claims plus
optimistic concurrency preserve two clerks' edits. Balanced allocation-set approval
confirms reviewed split costs atomically, releases source once and prevents later
extra full-amount costs. Supporting evidence does not become a second primary
charge. Adapt A1's single-primary compatibility pointer/readers rather than dropping
its uniqueness safeguard prematurely. Cross-mandate original access requires every
destination grant; scoped extracts need reviewed disclosure. Migration backfills
one-cost allocation sets only from proven links, preserving released columns.
Rollback prevents new allocation acceptance, retains adopted allocations/corrections.
A03/A04, RF09/RF10/RF25; tests for cents conservation, credit sign, split crash,
duplicate race, altered invoice, ambiguous address, no cross-scope auto-assignment
and source→cost retries across entry points. V01/V03 block unsupported tax treatment.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/04a-replacement-outcomes.md` schreiben; in `../state.md`
`current: steps/04b-assignment-mappings.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/04b-assignment-mappings.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/04b-assignment-mappings.md` anhand von state.md und contract.md weiter.“
