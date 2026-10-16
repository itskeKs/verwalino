# 21-ux-search — Package 21

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 20b-mobile-push. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Bestehende Low-input-/Paging-/Deep-link-/Fehlererhalt-/Accessibility-Anforderungen querprüfen, scoped Suche und Navigation nur im vorhandenen Designumfang verbessern. Keine zusätzlichen Features.

## Gezielt zu suchende Einstiegspfade

- `frontend/src/App.tsx`
- `frontend/src/api/`
- `backend/app/repositories/base.py`
- `docs/2026-10-01-product-ux-technical-design.md`

## Abnahmekriterien

Anforderungen des begrenzten Auftrags mit konkreten Datei-/Fixture-/Receiptlinks
belegen. Keine Scope-Erweiterung; Lücken/fehlende Freigaben bleiben explizit offen.
A31: negative Rechte-/Scopepfade; A32: Hash-/Geld-/Historyerhaltung bei Änderungen.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/21-ux-search.md` schreiben; in `../state.md`
`current: steps/22-security-operations.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/22-security-operations.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/22-security-operations.md` anhand von state.md und contract.md weiter.“
