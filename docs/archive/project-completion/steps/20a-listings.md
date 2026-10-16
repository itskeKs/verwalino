# 20a-listings — Package 20

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 19b-transfer-settings. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Public-field Allowlist, Disclosurepreview und Fake publish/update/withdraw mit Unknown-Outcome-Reconciliation.

## Gezielt zu suchende Einstiegspfade

- `backend/app/integrations/`
- `backend/app/api/units.py`
- `frontend/src/api/`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F7a — Listings.** P5.7 after F2/F1/B2. Add **new** `listing` modules,
`integrations/listings.py`, `api/listings.py`, `ListingsPage.tsx`: vacant unit →
approved public fields/photos → disclosure preview → explicit publish/update/
withdraw → reconciled status. Never include private tenant/financial/source metadata.
Migration preserves version/provider IDs; rollback stops future publication while
allowing an authorized withdrawal and status reconciliation. A29; RF03/RF30.
Fake tests cover approved-field allowlist, stale preview, uncertain retry and
withdrawal. V04/V06/V07 require provider license, sandbox and exact publishing scope.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/20a-listings.md` schreiben; in `../state.md`
`current: steps/20b-mobile-push.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/20b-mobile-push.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/20b-mobile-push.md` anhand von state.md und contract.md weiter.“
