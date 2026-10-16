# 15a-lease-lifecycle — Package 15

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 14b-sepa. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Parteien, versionierte Miete und explizite Vertragsaktivierung ohne doppelte C1-Obligation.

## Gezielt zu suchende Einstiegspfade

- `backend/app/models/lease_agreement.py`
- `backend/app/api/leases.py`
- `frontend/src/pages/UnitDetailPage.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F2 — Leases, co-tenants, deposits and handover data.** P5.1 after B4/C1/D1;
F1 supports documents. Extend `models/lease_agreement.py`, `api/leases.py`,
`services/payment_service.py`, `UnitDetailPage.tsx`, `PersonDetailPage.tsx`; add
**new** `lease_party`, `rent_schedule_version`, `deposit_movement`, `handover` modules,
`services/lease_lifecycle_service.py`, `LeaseLifecyclePage.tsx`. Explicit draft→
activation with parties/components/dates, base+NK obligations and flat/garage
grouping; prefill from source with contract-only vs master effective-date diff.
Deposit account/movements and disposition stay separate from rent and costs.
Termination/renewal/move-out preserves history and links keys/condition/readings
to D1; signatures attach through F4. Backfill the single current tenant as one
party only from evidence; obligation keys preserve C1 identity during schedule
upgrade. Rollback prevents new lifecycle transitions, never drops parties or
duplicates old obligations. A23/A05/A26; RF03/RF05–RF07/RF19.
Tests: co-tenants one obligation, future activation, overlap rejection, partial
first/last period, termination, deposit return/deductions and historical parties.
V01/V09 gate legal lifecycle terms; draft contract must never activate a second lease.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/15a-lease-lifecycle.md` schreiben; in `../state.md`
`current: steps/15b-deposits-handover.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/15b-deposits-handover.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/15b-deposits-handover.md` anhand von state.md und contract.md weiter.“
