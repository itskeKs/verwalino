# 18a-rent-adjustment — Package 18

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 17b-meetings. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Index-/Staffel-/Advance-Vorschlag mit Quelle, Effective-Date und notwendiger Notice/Consent; keine automatische Rechtswirkung.

## Gezielt zu suchende Einstiegspfade

- `backend/app/api/leases.py`
- `backend/app/services/settlement_service.py`
- `frontend/src/pages/UnitDetailPage.tsx`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**F3 — Rent/advance adjustment, steps and index proposals.** P5.2 after F2/D3.
Add **new** `rent_change_proposal`, `index_observation` modules,
`services/rent_adjustment_service.py`, `api/rent_changes.py`,
`RentAdjustmentPage.tsx`; reuse F1 templates/E4 delivery where notice is required.
Version clause/index series/base/input date, calculation basis and covered period.
Show effect on future schedule and uncertainty; actual activation requires approved
effective date and necessary notice/consent evidence. Settlement advance suggestion
is separate from finalizing the account; show costs/months/coverage before adoption.
No guessed current index or automatic legal effect. Additive proposal migration;
rollback cancels future proposals, leaves activated schedules and old obligations
intact. A24; RF06/RF07/RF16. Test rejected/late notice, corrected index, step retry,
prior obligations unchanged, and 100-water reference fixture with explicitly
reviewed advance basis (do not blindly adopt the observed 8.33). V09/V01 gate process;
V04 applies if a licensed external index source is chosen.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/18a-rent-adjustment.md` schreiben; in `../state.md`
`current: steps/18b-contracts-signing.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/18b-contracts-signing.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/18b-contracts-signing.md` anhand von state.md und contract.md weiter.“
