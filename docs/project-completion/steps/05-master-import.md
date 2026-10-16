# 05-master-import — Package 5

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 04e-exceptions-acceptance. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

B4-Import, Diff und resumierbare Einzelproperty-Abnahme implementieren bzw. Lücken schließen.

## Gezielt zu suchende Einstiegspfade

- `backend/app/api/properties.py`
- `backend/app/api/persons.py`
- `frontend/src/pages/PropertyDetailPage.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**B4 — Onboarding/master migration and portfolio.** P1.2 after B3. Add **new**
`master_import` / `setup_session` module families, `services/master_import_service.py`,
`api/master_imports.py`, `MasterImportPage.tsx`; extend property/unit/person/lease/
ownership APIs and `PropertyDetailPage.tsx`, `UnitDetailPage.tsx`, `App.tsx`/shell.
CSV/spreadsheet column maps and document candidates produce create/update/skip
diffs with external IDs, provenance, effective dates and dependency preview.
Accept one property transaction at a time; preserve rejected rows and resumability.
Do not merge people by name. Existing contacts without email remain valid.
Landlord guided setup and professional portfolio filters reuse scoped APIs; route
back/deep links keep context. Backfill import keys for imported records only;
pre-acceptance discard is safe, post-acceptance rollback is audited correction.
A05/A28, RF02/RF03/RF05/RF19/RF28; tests include identical reimport, overlapping
history, partial failure, master-change invalidation and bounded queries. No external
blocker; ownership/legal inference is not automatically accepted (V01).

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/05-master-import.md` schreiben; in `../state.md`
`current: steps/06-documents-templates.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/06-documents-templates.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/06-documents-templates.md` anhand von state.md und contract.md weiter.“
