# 00-start — Package 1

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: kein Vorgänger. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Arbeitsstand und Eigentümerschaft prüfen; tatsächliches HEAD und Migrationshead ermitteln. Package-1-Mapping und unzugewiesene V-Verantwortliche prüfen, Package-2-Transaktionsänderung gezielt testen. M-A/B1-Evidenz nur verifizieren, nicht blind neu implementieren. Fehlende Baseline-Abnahme vor abhängigen Änderungen nachholen.

## Gezielt zu suchende Einstiegspfade

- `docs/acceptance/parity-register.md`
- `docs/2026-10-03-paused-implementation-checkpoint.md`
- `backend/app/services/intake_service.py`
- `backend/app/core/tenant.py`

## Abnahmekriterien

Anforderungen des begrenzten Auftrags mit konkreten Datei-/Fixture-/Receiptlinks
belegen. Keine Scope-Erweiterung; Lücken/fehlende Freigaben bleiben explizit offen.
A31: negative Rechte-/Scopepfade; A32: Hash-/Geld-/Historyerhaltung bei Änderungen.

## Bei fehlender Baseline

Nur den passenden Reparaturplan laden, nicht alle fünf:
- [A1](../baseline/a1.md)
- [A2](../baseline/a2.md)
- [A3](../baseline/a3.md)
- [A4](../baseline/a4.md)
- [B1](../baseline/b1.md)

`current` auf den Reparaturplan und `return_to: steps/00-start.md` setzen;
vor Laden Kontextwechsel ausführen. Nach dessen Receipt zurück zu diesem Check.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/00-start.md` schreiben; in `../state.md`
`current: steps/03a-intake-recovery.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/03a-intake-recovery.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/03a-intake-recovery.md` anhand von state.md und contract.md weiter.“
