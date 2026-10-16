# 12b-sev-mixed-scope — Package 12

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 12a-settlement-manifests. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

SEV/WEG/Rental und Eigentümerwechsel getrennt integrieren; externe WEG-Komponenten einmal, keine Rechteübertragung zwischen Sphären.

## Gezielt zu suchende Einstiegspfade

- `backend/app/engine/weg.py`
- `backend/app/engine/settlement.py`
- `backend/app/engine/vermoegensbericht.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**D3 — Rental/WEG/SEV settlement packages and corrections.** P3.3 after D2.
Add **new** `settlement_batch`, `settlement_package_revision`, `artifact` modules,
`services/settlement_package_service.py`, `api/settlement_batches.py`,
`api/settlement_packages.py`, `AccountsBatchPage.tsx`, `SettlementPackagePage.tsx`.
Adapt `settlement_service.py`, `weg_settlement_service.py`,
`economic_plan_service.py`, their PDF services and existing account/detail pages.
Freeze permitted selection and collect per property/sphere/period; one package
contains all dependent recipient runs and engine-ready manifest. Reuse pure
`engine/settlement.py`, `weg.py`, `wirtschaftsplan.py`, `vermoegensbericht.py` and
snapshot/canonical/rounding utilities. Source→category→unit explanations include
excluded costs, coverage, days, rounding, plan Soll and separate payment arrears.
SEV imports eligible external WEG components once, avoids underlying-invoice
duplication and cannot grant access to the WEG sphere. Reserves/deposits stay out
of tenant operating costs. Asset reports reconcile bank/reserve/opening/closing
and receivable/payable evidence; incomplete full-WEG scope offers bounded SEV.
Prepare/review/approve uses exact revision and per-mandate V05 policy. Render
candidate PDFs outside transactions, then revalidate/serialize relevant writes
on a property/sphere revision counter and atomically commit all runs, manifests,
approval, hashes and artifact refs. Failure publishes nothing; staged orphan files
are inaccessible and safely retried/cleaned. Legacy finalize routes call this
policy; no bypass. Correction creates new runs/deltas while original PDF/hash and
delivery history remain frozen. Additive migration bridges new drafts; old
snapshots remain version-readable. Rollback pauses new package finalization and
keeps accepted revisions/files available.
A13–A16/A06/A32; RF04/RF14–RF16. Tests: 1,200 water over two 50 m² units gives 600,
302.47 tenant and 297.53 vacancy; advances 500 gives first-tenant balance 100.
Reference supplement: 100 water ×92/365 gives 25.21 tenant and 74.79 vacancy.
Add leap-year/date-boundary tests. WEG 2,400 costs/1,000 Soll each gives 200 each,
with second-owner 200 arrears separately when only 800 paid. SEV eligible/noneligible
and duplicate-source fixture; mixed scope, co-ownership and owner change;
stale finalization 409, crash/render failure, old PDF/hash unchanged and replay.
V01/V03/V05 must close relevant specialist/team release gates.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/12b-sev-mixed-scope.md` schreiben; in `../state.md`
`current: steps/12c-artifacts-corrections.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/12c-artifacts-corrections.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/12c-artifacts-corrections.md` anhand von state.md und contract.md weiter.“
