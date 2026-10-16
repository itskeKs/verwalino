# 17a-dunning — Package 17

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 16c-inbound-email. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Reconciled open items, Dispute und frozen Notice; vor E4-Release Balance/Rechte erneut prüfen.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/dunning_service.py`
- `backend/app/api/dunning.py`
- `backend/tests/test_m7_dunning.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**E2 — Dunning preparation.** P4.2 after C1/E1. Extend
`services/dunning_service.py`, `dunning_pdf.py`, `api/dunning.py`,
`models/dunning_notice.py`, `DunningNoticesPage.tsx`, `DunningNoticeDetailPage.tsx`;
add **new** dispute/policy-version and notice-input-revision records. Prepare from
reconciled open items, freeze stage/template/balance and identify disputed items.
E2 can save notices locally; connect release to E4 when available. Recheck balance
and permissions immediately before releasing to delivery; a new payment invalidates
the draft. Append evidence to new notices; preserve old ones and retain disputes
on rollback. A19; public baseline, RF08/RF17 context only. Extend `test_m7_dunning.py`
for new payment/stale notice, dispute pause, stage repetition and no implicit send.
V01/V09 require approved fee/date/process policy; no invented statutory defaults.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/17a-dunning.md` schreiben; in `../state.md`
`current: steps/17b-meetings.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/17b-meetings.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/17b-meetings.md` anhand von state.md und contract.md weiter.“
