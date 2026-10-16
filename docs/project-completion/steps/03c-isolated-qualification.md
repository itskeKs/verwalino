# 03c-isolated-qualification — Package 3

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 03b-intake-jobs. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Isolierte Runtime-Qualifikation der Scanner-/OCR-Worker-Voraussetzungen:
- `backend/app/core/intake_inspection.py` auf korrekte Bounds, MIME-Sniffing, Malware-Scanner-Integration
- `backend/app/workers/workflow_runner.py` auf Jobs/Outbox-Integration, scoped claims, leases/fencing
- `Dockerfile` auf Build-Konsistenz und Abhängigkeiten
- Vorhandene Tests für diese Komponenten prüfen, keine neuen Tests schreiben

Nicht bereitgestellte Scanner/Freigaben blockiert dokumentieren. B2-Status ehrlich schließen oder lokal abgeschlossen/release-blockiert übergeben.

## Gezielt zu prüfende Einstiegspfade

- `backend/app/core/intake_inspection.py` (existiert, gelesen)
- `backend/app/workers/workflow_runner.py` (existiert, muss geprüft werden)
- `Dockerfile` (existiert, muss geprüft werden)
- `backend/tests/` nach Tests für intake_inspection, workflow_runner

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/03c-isolated-qualification.md` schreiben; in `../state.md`
`current: steps/04a-replacement-outcomes.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/04a-replacement-outcomes.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/04a-replacement-outcomes.md` anhand von state.md und contract.md weiter.“
