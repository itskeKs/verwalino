# 24-final-gate — Package 24

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 23b-quality-parity. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Alle supported Journeys integriert verifizieren; offene lokale Aufgaben und Releasegates aufführen. Erst bei echten V-Freigaben/Operationsnachweisen Releasebereitschaft behaupten; Push/Deploy bleiben separat autorisierungspflichtig.

## Gezielt zu suchende Einstiegspfade

- `docs/acceptance/parity-register.md`
- `docs/project-completion/state.md`
- `docs/project-completion/receipts/`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**G2 — Quality, usability and parity sign-off.** Add **new**
`docs/acceptance/parity-register.md` with one entry per P-row and linked A fixtures,
RF depth, R/O/D/V classification, supported/excluded cases, implementation commit,
check environment/result and responsible approver. All rows start planned; no
historical passing suite marks a new capability delivered. Evaluate ≥200 sanitized
mixed documents with class/eligibility denominators, first-run versus known context,
wrong-scope assignments, corrections and typed fields. Known supported invoice
target: median zero typed fields, ≥80% prepared without correction, explicit
financial review. Zero wrong cross-scope assignments in release fixtures is
mandatory; ≥99.5% precision needs a larger labeled set and uncertainty reporting,
not a claim inferred from 200 items. Observe ≥5 landlords and ≥5 professional
operators; proposed target ≥50% fewer typed fields with seeded errors still found.
Complete account fixtures need selection/review only; incomplete ones expose every
seeded blocker. Independent accounting/security review, V sign-offs and runnable
row-level evidence decide release. No schema migration; rollback withdraws the
unsupported claim/feature flag, preserving records. V01–V09 and all A rows apply;
untested reference groups remain publicly documented coverage, never full parity.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Kein weiterer Teilplan. Receipt und State schreiben; tatsächliche lokale
Fertigstellung, offene Fach-/Provider-/Opsgates und autorisierten Releasezustand
berichten. Bei offenen Gates kein „Projekt vollständig abgeschlossen“.
