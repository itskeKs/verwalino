# 18b-contracts-signing — Package 18

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 18a-rent-adjustment. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Versionierte Vertragsartefakte, contract-only vs canonical Diff, Fake-Signing, partial signer/stale callback; keine Leaseaktivierung durch Entwurf.

## Gezielt zu suchende Einstiegspfade

- `backend/app/api/leases.py`
- `backend/app/integrations/`
- `frontend/src/api/`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F4 — Contract generation, signing and handover outputs.** P5.4 after F1/F2/E4.
Add **new** `contract_draft`, `signature_request` module families,
`services/contract_service.py`, `integrations/signing.py`, `api/contracts.py`,
`ContractsPage.tsx`, `ContractReviewPage.tsx`; reuse handover records for rendered
protocols. Merge approved data into versioned templates, display required missing
fields before long review, and explicitly choose contract-only or canonical update.
Freeze exact terms/parties/artifact hash per signing request; changed terms invalidate
the request. Verified callback/status reconciles partial signers and signed copies;
download failure is visible/retryable. No purchase/sign/send implicit in draft save.
Migration stores revisions, never activates leases on backfill. Rollback stops
new requests and keeps signed hashes/evidence. A26/A23/A05; RF18/RF19/RF29.
Test 2-room document-only change leaves unit unchanged; different contract start
leaves active lease intact; missing required data, partial signatures, stale callback
and callback replay. V04/V06/V07/V09 gate provider/signature level/reference scope
and legal templates. Local generation and fake signing can proceed first.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/18b-contracts-signing.md` schreiben; in `../state.md`
`current: steps/19a-finance-exports.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/19a-finance-exports.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/19a-finance-exports.md` anhand von state.md und contract.md weiter.“
