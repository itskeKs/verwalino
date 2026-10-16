# 09-cases-tasks — Package 9

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 08b-external-allocations. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Case-/Task-/Deadline-Journey; fachliche Exceptions bleiben unabhängig blockierend.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/fristen_service.py`
- `frontend/src/pages/FristenmonitorPage.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**E1 — Cases, tasks and deadlines.** P4.1 after M-B. Add **new** `case`, `task`,
`case_template` module families, `services/case_service.py`, `api/cases.py`,
`CasesPage.tsx`, `CaseDetailPage.tsx`; adapt `fristen_service.py` and
`FristenmonitorPage.tsx` to propose stable source-key tasks. Keep business exceptions
linked to cases rather than replacing their blocking policy. Required tasks plus
recorded outcome permit closure; optional unfinished tasks do not block by default.
Manual reopening and template-revision adoption retain history, assignees and due
dates. Snoozing/closing cannot clear accounting checks. Additive migration; rollback
stops generation but preserves cases/logs and source deadlines. A18; RF01/RF17/RF23.
Test one deadline→one task, required/optional completion, outcome, reopen, template
change and unrelated financial blocker. No provider blocker.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/09-cases-tasks.md` schreiben; in `../state.md`
`current: steps/10-recurring-cashflow.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/10-recurring-cashflow.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/10-recurring-cashflow.md` anhand von state.md und contract.md weiter.“
