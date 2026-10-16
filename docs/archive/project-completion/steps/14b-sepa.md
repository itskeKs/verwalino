# 14b-sepa — Package 14

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 14a-bank-provider. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Schema-/Profilgeprüfte SEPA-Vorschau, immutable Instruction und Export/Reconciliation; Export ist niemals bezahlt.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/payment_service.py`
- `backend/app/integrations/`
- `docs/acceptance/parity-register.md`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**C4 — SEPA export and reconciliation.** P2.4 after C1's obligation keys; independent
of C3 and full rental lifecycle. Add **new** `sepa_mandate`, `sepa_batch` modules,
`services/sepa_service.py`, `api/sepa_exports.py`, `SepaExportPage.tsx`. Capture
creditor/mandate revision/sequence, eligible due items, immutable batch instruction
IDs, preview totals, approval and artifact hash. Repeated download returns the same
artifact; amended/returned collection is linked explicitly. XML release never
marks an obligation paid. Migrate only evidenced mandates; rollback stops new
release, keeps existing instructions reconcilable. A10; RF27 observed controls.
Test bank-supported XML schema/profile, duplicate export, revoked mandate,
return/retry and money invariants. V03/V04 and specialist mandate/pre-notification
requirements block live release, not fake/schema-backed implementation.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/14b-sepa.md` schreiben; in `../state.md`
`current: steps/15a-lease-lifecycle.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/15a-lease-lifecycle.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/15a-lease-lifecycle.md` anhand von state.md und contract.md weiter.“
