# 11b-readiness — Package 11

## Start / ausschließlich laden

`../contract.md`, `../state.md` und diesen Plan. Letzte Receipt nur bei relevanter
Dependency lesen. Vorgänger: 11a-rules. Keine Gesamtplanlektüre.

## Begrenzter Auftrag

Readiness und reverse source dependencies integrieren; relevante Änderungen stalen Drafts und erzeugen Korrekturaufgaben, legacy finalizer behalten Coverage-Guard.

## Gezielt zu suchende Einstiegspfade

- `backend/app/services/financial_input_policy.py`
- `backend/app/services/settlement_service.py`

## Verbindlicher Teilauftrag aus dem Oktoberplan

Dieser Vertrag beschreibt den Gesamtumfang des genannten Units. **In dieser
Sitzung nur den begrenzten Auftrag oben ausführen**; andere Stufen bleiben beim
Nachfolger. Bereits gelieferte Module verifizieren, nicht duplizieren.

**D2 — Readiness, source dependencies and executable rules.** P3.2 after C1/D1/B3.
Extend `models/property_rule.py` and property-knowledge boundaries; add **new**
`executable_rule_version`, `source_dependency`, `expected_item` module families,
`services/rule_compiler.py`, `services/readiness_service.py`,
`api/accounting_rules.py`, `api/readiness.py`, `ReadinessPage.tsx` and
`AccountingRulesPage.tsx`. Generalize A3 coverage, keeping legacy finalizers on the
same policy. Use a typed operation vocabulary, source hashes, dates, priority,
parameters, fixture evidence and approval; no free-text/model-generated execution.
Approved knowledge defaults to not evaluated, never activated. Competing rules
block; mid-period activation slices inputs. Readiness covers scope/history, costs,
expected-document applicability attestation, account/advance coverage, readings,
rules, reconciliations and anomalies with affected outputs/resolution links.
Expected items are suggestions; absence alone proves neither completeness nor a
debt. Indexed reverse dependencies stale drafts and create correction cases for
finals. Migration adds versioned dependencies/coverage, never rewrites final
manifests. Rollback disables future activation and keeps historical versions.
A06/A12/A31/A32; RF04/RF14/RF15/RF25. Test knowledge-approved-but-inactive, activated
rule effect, conflict, missing coverage, scope denial and multi-draft invalidation.
V01/V03/V05 gate interpretation, periods and team approvals.

## Prüfen und abgrenzen

1. Tatsächlichen Code/Status prüfen; den nächsten **nachgewiesenen** Gap auswählen.
2. Regression + kleinste passende Änderung; gezielte Backend/DB/UI-Tests, echte
   Browserjourney für geänderte Integration. Shared Engine/Auth/Jobs bei Änderung
   proportional breiter prüfen. Nie einen nicht ausgeführten Test als bestanden zählen.
3. Lokalen Teilumfang und noch offene Gesamtunit-/V-Gates getrennt dokumentieren.
   Keine Produktions- oder vollständige Paritybehauptung aufgrund lokaler Tests.

## Verpflichtende Übergabe — nächsten Plan aufrufen

Nach lokaler Abnahme: Receipt `../receipts/11b-readiness.md` schreiben; in `../state.md`
`current: steps/12a-settlement-manifests.md` und den Receiptlink setzen. Bei ungeklärtem lokalen
Blocker `current` hier behalten; nur unabhängigem Nachfolger ausdrücklich folgen.
**Jetzt Kontextwechsel nach contract.md ausführen. Erst danach**
`steps/12a-settlement-manifests.md` laden und dessen Auftrag bearbeiten. Nicht alle nachfolgenden
Pläne vorab laden. Übergabe für frische Sitzung: „Arbeite ab
`docs/project-completion/steps/12a-settlement-manifests.md` anhand von state.md und contract.md weiter.“
