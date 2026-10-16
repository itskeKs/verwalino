# 07c-money-reconciliation — Package 7

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 07b-obligations. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Reviewte Allokationen mit Restbetrag, Teilzahlung, Refund, Reversal und manueller Altzahlungsversöhnung; genau einmal buchen. Gesamte C1-Journey prüfen.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/payment_service.py`
- `backend/app/repositories/payment_repo.py`
- `frontend/src/pages/MietkontoPage.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**C1 — File imports, obligations and reviewed matching.** P2.1 after B1/B2 and M-A;
B3 supplies shared exceptions. Extend `models/payment.py`, `models/bank_account.py`,
`services/payment_service.py`, `repositories/payment_repo.py`, `engine/mietkonto.py`
through adapters; add **new** `bank_import`, `bank_transaction`, `obligation`,
`transaction_allocation`, `payment_reconciliation_link` module families and
`api/bank_imports.py`, `api/transaction_allocations.py`. Add **new**
`BankImportPage.tsx`, `TransactionMatchingPage.tsx`; adapt `MietkontoPage.tsx`,
`PaymentsPage.tsx`, `OpenItemsPage.tsx` and `BankAccountsPage.tsx`.
Support declared CSV mappings and a fixture-backed CAMT profile; pin exact supported
versions before enabling each parser. Preview encoding/separator/date/decimal,
unknown columns, row count, debit/credit totals, account/currency and coverage.
Rejected/empty imports create no transactions. Raw facts stay immutable; reliable
provider IDs precede fingerprints, identical real transfers remain distinguishable.
Create stable component/period obligations from existing leases/final economic
plans; keep due, booking, value and credited-obligation dates distinct.
Confirm allocations into existing booked payments once, with remainder and reversal
links. Existing manually booked payments are reconciled, not booked again; match
account/currency/direction/amount/component/period and forbid a second claim.
Paid-through assertions/opening balances remain separate from bank proof. Migrate
year-only data only for supported full-year coverage; C1 extends A3 to exact periods.
Rollback pauses imports/matching, preserving raw data, links and booked money.
A07/A06/A28; RF07/RF08/RF11/RF12/RF23. **New** `test_bank_imports.py` and
`test_transaction_allocations.py` cover 650→600 base+50 NK, 750→700+50 unapplied,
combined flat/garage and months, partial refund/return, concurrent manual adoption,
overlapping imports and balance controls. V03 gates period policy; file parsing
does not need a live bank provider.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/07c-money-reconciliation.md` schreiben; in `../state.md`
`current: steps/08a-meters.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/08a-meters.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/08a-meters.md` anhand von state.md und contract.md weiter.“
