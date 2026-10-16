# Crossreview-Befunde mit Qwen3.6 und 49k beheben — Reparaturkette

Diese separate Reparaturkette setzt den gespeicherten Plan
`crossreview-remediation-qwen36-49k` um. Sie ist unabhängig von der
Produkt-Roadmap ab Schritt 04b.

## Zweck

Behebung der im Crossreview identifizierten Dokumentationsfehler,
sicherungsrelevanter Evidenzlücken und fachlicher Verdachtsfälle
(WEG-Rücklagen, Doppeladoption, Scanner-Recovery) ohne eigenmächtige
Finanzänderungen.

## Arbeitsprinzipien

- Ausschließlich diesen Plan, `state.md`, `contract.md` und **einen**
  aktuellen Teilplan laden.
- Kontextbudget: Ziel ~49k Tokens, Arbeitsziel ~28.000 belegte Tokens.
- Pro Sitzung höchstens ein kohärenter Schritt.
- Nach jedem Schritt: Receipt schreiben, `state.md` aktualisieren,
  Kontextwechsel durchführen.
- Keine fremden Änderungen, keine Produktionsaktionen, keine automatischen
  Commits oder Pushes.
- Keine Fortsetzung der Produkt-Roadmap ab 04b.

## Arbeitspakete

| Nr. | Beschreibung | Status |
|-----|-------------|--------|
| 01 | Ausgangszustand sichern und separate Reparaturkette anlegen | pending |
| 02 | Statusautorität und Modell-/Kontextvertrag klären | pending |
| 03 | 03a-Receipt und B2-Restpunkte reparieren | pending |
| 04 | Parity-Register konsolidieren | pending |
| 05 | Deployment-Runbook berichtigen | pending |
| 06 | 04a-Evidenz und Reproduzierbarkeit sichern | pending |
| 07 | WEG-Rücklagenfehler qualifizieren; Fachgate respektieren | pending |
| 08 | Doppeladoption und Scanner-Recovery getrennt untersuchen | pending |
| 09 | Endprüfung und ehrlichen Goal-Abschluss erstellen | pending |

## Freigabegates

- **V01**: Accounting/Legal — WEG, Rücklagen, gemischte Scopes, Golden Cases.
  Ohne V01-Freigabe keine eigenmächtige Formel- oder Rücklagenänderung.
- **V03**: Accounting/Perioden/Steuer.
- **V05**: Org-Admin, versionierte Teamfreigabe.
- **Externes Zwei-Reviewer-Gate**: `.coddy/crossreview.json` verlangt
  `min_reviewers: 2`, ist aber nur mit `claude:opus` konfiguriert — bleibt offen.

## Ergebnis

Vollständig erledigt nur bei erfüllten Plan-Abnahmen. Fachliche oder Test-
blocker bedeuten `blocked` oder `open`, nicht vollständigen Erfolg.
