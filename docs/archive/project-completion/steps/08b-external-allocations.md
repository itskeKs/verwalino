# 08b-external-allocations — Package 8

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 08a-meters. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Externe periodische Komponententotale mit Quellenkeys versöhnen, 60+40 akzeptieren/60+50 sperren; External/Internal-Kollision verhindern.

## Gezielt zu suchende Einstiegspfade

- `backend/app/models/meter.py`
- `backend/app/services/settlement_service.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**D1 — Meter history and external allocations.** P3.1 after M-B. Extend existing
`models/meter.py`; add **new** `repositories/meter_repo.py`, `services/meter_service.py`,
`api/meters.py`, `api/meter_readings.py`, `external_allocation_batch` module family,
`services/external_allocation_service.py`, `MetersPage.tsx` and
`ExternalAllocationsPage.tsx`. Persist units/multipliers/type defaults/topology,
validity/replacement/rollover, recorder/date/photo source and measured/estimated
method approval. First reading 100 m³ supplies no fabricated usage. A reading
revision preserves originals; missing tenant-change boundaries block affected
methods. Provider/WEG statements import period/unit/component totals and source
keys; 60+40 against 100 accepts, 60+50 blocks. Enforce one source mode per component
(external vs internal) and source-key duplicate protection. Backfill existing
readings as unverified where units/provenance are absent; rollback keeps evidence
and stops new acceptance. A11; RF13 plus RF07 unverified method claims.
Tests include 100→160, replacement, rollover, estimate approval, duplicate import,
photos and external/internal collision. V01/V02 block unsupported methods, not
supported external reconciliation.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/08b-external-allocations.md` schreiben; in `../state.md`
`current: steps/09-cases-tasks.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/09-cases-tasks.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/09-cases-tasks.md` anhand von state.md und contract.md weiter.“
