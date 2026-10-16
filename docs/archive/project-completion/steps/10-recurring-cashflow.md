# 10-recurring-cashflow — Package 10

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 09-cases-tasks. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Versionierte Wiederholung, Darlehensplan und Cashflow; Soll/Forecast/Cash getrennt, keine historischen Umschreibungen.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/payment_service.py`
- `frontend/src/pages/DashboardPage.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**C2 — Recurrence, loans and cashflow.** P2.3 after C1. Add **new**
`recurring_finance`, `loan_schedule` module families, `services/cashflow_service.py`,
`api/finance_forecasts.py` and `FinanceForecastPage.tsx`; extend `DashboardPage.tsx`.
Version approved templates and source loan schedules; stable period keys generate
obligations/cost drafts only. Separate principal/interest/fees/reserve transfers,
forecast/Soll/booked cash and owner scope. Schedule amendments show effective-date
diffs and require acceptance; no retrospective rewrite. Additive migration; cancel
future generation on rollback, preserve existing drafts and approved schedule
versions. A09; RF01/RF23, public baseline for unexercised loan execution. Test month
boundaries, schedule rerun, variable terms, principal exclusion and dashboard
reconciliation. V01/V03 gate financial/tax interpretation.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/10-recurring-cashflow.md` schreiben; in `../state.md`
`current: steps/11a-rules.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/11a-rules.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/11a-rules.md` anhand von state.md und contract.md weiter.“
