# 04d-credits-replacements — Package 4

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 04c-multiscope-allocation. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Typisierte Gutschriftbeziehungen und verbleibende Visual-/Cross-Scope-Replacements ergänzen. Gruppierte Korrekturen, physische Claims, alte Hashes und Folgearbeiten erhalten; keine Doppelbelastung.

## Gezielt zu suchende Einstiegspfade

- `docs/2026-10-03-allocation-source-replacements.md`
- `docs/2026-10-03-allocation-correction-design.md`
- `backend/app/api/allocation_adoptions.py`
- `backend/app/api/exceptions.py`

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

Nach lokaler Abnahme: Receipt `../receipts/04d-credits-replacements.md` schreiben; in `../state.md`
`current: steps/04e-exceptions-acceptance.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/04e-exceptions-acceptance.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/04e-exceptions-acceptance.md` anhand von state.md und contract.md weiter.“
