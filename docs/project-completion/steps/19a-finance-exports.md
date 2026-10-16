# 19a-finance-exports — Package 19

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 18b-contracts-signing. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Versionierte Finance-Exporter und unveränderliche Artefakte, Mapping-/VAT-/Importer-Gates; keine zweite Buchhaltung.

## Gezielt zu suchende Einstiegspfade

- `backend/app/exports/`
- `backend/app/services/payment_service.py`
- `docs/acceptance/parity-register.md`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F5 — Finance reports, DATEV, EÜR and Anlage V.** P5.5 after C1/C2/F1, with D3
for account-based outputs. Add **new** `export_profile`, `export_job` modules,
`services/finance_export_service.py`, versioned `exports/` adapters,
`api/exports.py`, `FinanceExportsPage.tsx`. Map accounting basis, chart/account/tax
codes/effective dates per sphere; preview unmapped lines and missing receipts.
Produce immutable reconciled CSV/ZIP/PDF/XLSX as applicable with artifact receipt,
manifest and stable repeat download; corrections create a new export revision.
Reports project the existing ledger, never create a second ledger or consolidate
mandate cash. Supported VAT profiles must be explicit; RF26's reference limitation
does not authorize an incorrect export. Migration adds versioned mappings/artifacts;
rollback stops releases, preserves accepted outputs. A27; RF25/RF26/RF31.
Test mapped/unmapped VAT, missing receipt, totals, separate mandates, repeat export,
failure/retry and target importer/schema validation. Adviser-approved EÜR/Anlage V
fixtures and target DATEV acceptance are V03/V01 blockers, not replaced by CSV tests.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/19a-finance-exports.md` schreiben; in `../state.md`
`current: steps/19b-transfer-settings.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/19b-transfer-settings.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/19b-transfer-settings.md` anhand von state.md und contract.md weiter.“
