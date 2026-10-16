# 13-heating-co2 — Package 13

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 12c-artifacts-corrections. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Nur V02-freigegebene Methoden und Golden Cases implementieren. Ohne V02 keine Formel erfinden: offenen Gate und externe Alternative dokumentieren.

## Gezielt zu suchende Einstiegspfade

- `backend/app/engine/`
- `backend/app/models/meter.py`
- `docs/acceptance/parity-register.md`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**D4 — Supported internal heating and CO2.** P3.4 after D1–D3 and approved V02
fixtures. Add **new** `engine/heating.py`, `engine/co2.py`, supported-method schemas,
`services/heating_input_service.py` and method/evidence controls in the account
workspace. Pin jurisdiction/effective-date applicability, exceptions and complete
required inputs before implementing a method. Explicitly select internal method
or accepted provider components; no double counting or generic day-weight fallback.
Version method/input/output in the manifest; migration adds method metadata,
rollback disables new calculation but retains old replay code and results.
A17; no authenticated proof of reference calculator accuracy. Specialist-labeled
golden cases include user change, missing readings/consumption, exceptions and
CO2 tenant/owner split; unsupported cases block or use approved external inputs.
V02 is a hard calculator release blocker; this plan invents no formula or legal rule.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/13-heating-co2.md` schreiben; in `../state.md`
`current: steps/14a-bank-provider.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/14a-bank-provider.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/14a-bank-provider.md` anhand von state.md und contract.md weiter.“
